"""Integration subset: historical CSV replay only; no fabricated analysis."""
import asyncio,json,time,uuid,math
from bisect import bisect_left, bisect_right
import os
import threading
from functools import wraps
from .analysis import Engine, load_config
from pathlib import Path
from fastapi import FastAPI,HTTPException,Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel,ConfigDict
from .core import connect,ingest,load_calibration,derive
ROOT=Path(__file__).resolve().parents[1]
DB_PATH=Path(os.getenv('DB_PATH', str(ROOT/'data/backend.sqlite3')))
app=FastAPI(title='FCD integration subset',version='0.3.0')
app.add_middleware(CORSMiddleware,allow_origins=[x.strip() for x in os.getenv('CORS_ORIGINS','http://localhost:5173,http://127.0.0.1:5173').split(',') if x.strip()],allow_methods=['GET','POST','PATCH'],allow_headers=['Content-Type'])
sessions={}
LOCK=threading.RLock()
def synchronized(fn):
 @wraps(fn)
 def wrapped(*args,**kwargs):
  with LOCK:return fn(*args,**kwargs)
 return wrapped

def rules_config():return load_config(ROOT/'rules.json')
def persist(events):
 if not events:return
 db=database()
 try:
  with db:
   db.execute('CREATE TABLE IF NOT EXISTS analysis_events (id TEXT PRIMARY KEY, session_id TEXT, generation INTEGER, payload TEXT NOT NULL)')
   for e in events:db.execute('INSERT OR REPLACE INTO analysis_events VALUES (?,?,?,?)',(e['id'],e['session_id'],e['generation'],json.dumps(e,ensure_ascii=False)))
 finally:db.close()

def setup_analysis(s):
 cfg=rules_config()
 active=s['_kind']=='experiment' and cfg['enabled'] and bool(cfg['rules'])
 s['_engine']=Engine(cfg,s['id'],s['generation'],s['_from']) if active else None
 s['_cursor']=bisect_left([r['ts'] for r in s['_rows']],s['_from'])
 s['analysis_status']='warming_up' if active else 'disabled'
 s['analysis_reason']='unvalidated_rules' if active else 'format_only' if s['_kind']=='sample' else 'calibration_only' if s['_kind']=='calibration' else 'rules_not_configured'
def analyze(s):
 engine=s.get('_engine')
 if not engine or not s['_played']:return
 while s['_cursor']<len(s['_rows']):
  r=s['_rows'][s['_cursor']]
  if r['ts']>s['virtual_ts']:break
  persist(engine.consume(r,derive(r,s['_cal'],s['_kind'],s['start_ts'])))
  s['_cursor']+=1
 s['analysis_status']=engine.status
 if s['state']=='completed':persist(engine.close(s['virtual_ts'],'dataset_ended'))

units=dict(zip('temp humid eco2 tvoc pm1 pm25 pm10 motion co2 dp'.split(),['°C','%','ppm','ppb','µg/m³','µg/m³','µg/m³','0/1','ppm','Pa']))
def wrap(d):return {'data':d,'meta':{'request_id':str(uuid.uuid4())}}
def database():
 DB_PATH.parent.mkdir(parents=True, exist_ok=True)
 return connect(DB_PATH)
def datasets():
 db=database()
 try:return [dict(x) for x in db.execute('SELECT * FROM datasets ORDER BY id')]
 finally:db.close()
@app.on_event('startup')
def initialize():
 db=database()
 try:
  if not db.execute('SELECT 1 FROM datasets LIMIT 1').fetchone():
   for f,k in [('sample_5min.csv','sample'),('calib_20261113.csv','calibration')]:ingest(db,ROOT/'data'/f,k)
 finally:db.close()
@app.get('/api/v1/health')
def health():return wrap({'status':'ok','version':'0.3.0','server_ts':time.time(),'capabilities':{'live':False,'replay':True,'analysis':bool(rules_config()['enabled'] and rules_config()['rules']),'labels':False}})
@app.get('/api/v1/datasets')
def list_datasets():return wrap([{'id':str(d['id']),'name':d['name'],'kind':d['kind'],'start_ts':d['start_ts'],'end_ts':d['end_ts']} for d in datasets()])
class Create(BaseModel):
 model_config=ConfigDict(extra='forbid', allow_inf_nan=False)
 mode:str='replay'
 dataset_id:str
 speed:float=1
class Control(BaseModel):
 model_config=ConfigDict(extra='forbid', allow_inf_nan=False)
 action:str
 ts:float|None=None
 speed:float|None=None

def get(sid):
 if sid not in sessions:raise HTTPException(404,'Session not found (server restart clears sessions)')
 return sessions[sid]
@synchronized
def tick(s):
 if s['state']=='running':
  s['virtual_ts']=min(s['end_ts'],s['_anchor_ts']+(time.monotonic()-s['_anchor_clock'])*s['speed'])
  if s['virtual_ts']>=s['end_ts']:s['state']='completed'
 analyze(s)
 return s
def public(s):return {k:v for k,v in tick(s).items() if not k.startswith('_')}
@app.post('/api/v1/sessions',status_code=201)
@synchronized
def create(b:Create):
 if b.mode!='replay' or b.speed not in [.5,1,2,4,5,10]:raise HTTPException(422,'Only replay; supported speeds .5/1/2/4/5/10')
 d=next((d for d in datasets() if str(d['id'])==b.dataset_id),None)
 if not d:raise HTTPException(404,'Dataset not found')
 db=database()
 try:rows=[json.loads(r['raw']) for r in db.execute('SELECT m.raw FROM measurements m JOIN membership x ON m.node=x.node AND m.boot=x.boot AND m.seq=x.seq WHERE x.dataset_id=? ORDER BY m.ts,m.node',(d['id'],))]
 finally:db.close()
 sid=str(uuid.uuid4());now=time.time()
 s=dict(id=sid,mode='replay',dataset_id=b.dataset_id,state='paused',speed=b.speed,virtual_ts=d['start_ts'],start_ts=d['start_ts'],end_ts=d['end_ts'],generation=1,analysis_status='disabled',analysis_reason='analysis_not_implemented',created_at=now,updated_at=now,_rows=rows,_cal=load_calibration(ROOT/'calib_params_v2.json'),_kind=d['kind'],_anchor_ts=d['start_ts'],_anchor_clock=time.monotonic(),_from=d['start_ts'],_played=False)
 s['_node_rows']={n:[r for r in rows if r['node']==n] for n in ['node-a','node-b']}
 s['_node_times']={n:[r['ts'] for r in rs] for n,rs in s['_node_rows'].items()}
 setup_analysis(s)
 sessions[sid]=s;return wrap(public(s))
@app.get('/api/v1/sessions/{sid}')
def session(sid:str):return wrap(public(get(sid)))
@app.patch('/api/v1/sessions/{sid}/replay')
@synchronized
def control(sid:str,b:Control):
 s=tick(get(sid))
 if b.action not in ['play','pause','seek','stop','set_speed']:raise HTTPException(422,'Invalid action')
 if b.action=='seek':
  if b.speed is not None or b.ts is None or not math.isfinite(b.ts) or not s['start_ts']<=b.ts<=s['end_ts']:raise HTTPException(422,'Invalid seek')
  if s.get('_engine'):persist(s['_engine'].close(s['virtual_ts'],'seek_reset'))
  s.update(virtual_ts=b.ts,_from=b.ts,_played=False,generation=s['generation']+1,state='paused')
  setup_analysis(s)
 elif b.action=='set_speed':
  if b.ts is not None or b.speed not in [.5,1,2,4,5,10]:raise HTTPException(422,'Invalid speed')
  if s['state'] not in ['running','paused']:raise HTTPException(409,'Seek before restart')
  s['speed']=b.speed
 else:
  if b.ts is not None or b.speed is not None:raise HTTPException(422,'Unexpected action fields')
  if b.action in ['play','pause'] and s['state'] in ['stopped','completed']:raise HTTPException(409,'Seek before restart')
  s['state']={'play':'running','pause':'paused','stop':'stopped'}[b.action]
  if b.action=='play':s['_played']=True
  if b.action=='stop' and s.get('_engine'):persist(s['_engine'].close(s['virtual_ts'],'session_stopped'))
 s.update(_anchor_ts=s['virtual_ts'],_anchor_clock=time.monotonic(),updated_at=time.time())
 return wrap(public(s))
def eligible(s):
 if not s['_played']:return []
 return (r for r in s['_rows'] if s['_from']<=r['ts']<=s['virtual_ts'])
def window(s,node,start,end):
 if not s['_played']:return []
 times=s['_node_times'][node]
 return s['_node_rows'][node][bisect_left(times,max(start,s['_from'])):bisect_right(times,end)]
@synchronized
def snapshot(s):
 tick(s);last={}
 if s['_played']:
  for n,times in s['_node_times'].items():
   i=bisect_right(times,s['virtual_ts'])-1
   if i>=0 and times[i]>=s['_from']:last[n]=s['_node_rows'][n][i]
 nodes={}
 for n in ['node-a','node-b']:
  r=last.get(n);p=derive(r,s['_cal'],s['_kind'],s['start_ts']) if r else None
  gap=bool(r and s['virtual_ts']-r['ts']>5)
  mm={}
  for k,u in units.items():
   q='missing';v=None
   if p:v=p['values'][k];q=p['quality'].get(k,'ok')
   if n=='node-b' and k=='humid':q='disabled'
   elif n=='node-b' and k in ['co2','dp']:q='not_installed'
   elif gap:q='stale'
   elif v is None:q='missing'
   elif q=='saturated':pass
   elif p and p['calibration_warmup']:q='warmup'
   mm[k]={'value':v,'unit':u,'quality':q,'last_valid_ts':r['ts'] if r and q=='ok' else None}
  nodes[n]={'node':n,'last_sample_ts':r['ts'] if r else None,'connection':'unknown','replay_data_state':'not_started' if not r else 'gap' if gap else 'normal','metrics':mm}
 return dict(session_id=s['id'],generation=s['generation'],mode='replay',as_of_ts=s['virtual_ts'],nodes=nodes,analysis=s['_engine'].summary() if s.get('_engine') else {'status':'disabled','decision_status':'disabled','cause':None,'confidence':None,'method':'none','evidence':[]},active_event_count=len(s['_engine'].active) if s.get('_engine') else 0)
@app.get('/api/v1/sessions/{sid}/snapshot')
def snap(sid:str):return wrap(snapshot(get(sid)))
@app.get('/api/v1/sessions/{sid}/series')
def series(sid:str,node:str,sensor:str,start_ts:float,end_ts:float,bucket_seconds:int=Query(1,ge=1),max_points:int=Query(2000,ge=1,le=5000)):
 s=tick(get(sid))
 if node not in ['node-a','node-b'] or sensor not in units or sensor=='humid' or (node=='node-b' and sensor in ['co2','dp']):raise HTTPException(422,'Unsupported sensor')
 if not all(math.isfinite(x) for x in [start_ts,end_ts]) or start_ts>end_ts or end_ts>s['virtual_ts']:raise HTTPException(422,'Invalid time range')
 count=max(1,math.ceil((end_ts-start_ts)/bucket_seconds))
 if count>max_points:raise HTTPException(413,'Increase bucket_seconds')
 bins=[[] for _ in range(count)];flags=[[] for _ in range(count)]
 for r in window(s,node,start_ts,end_ts):
  p=derive(r,s['_cal'],s['_kind'],s['start_ts']);i=min(count-1,int((r['ts']-start_ts)//bucket_seconds));v=p['values'][sensor]
  q='missing' if v is None else 'saturated' if p['quality'].get(sensor)=='saturated' else 'warmup' if p['calibration_warmup'] else 'ok'
  flags[i].append(q)
  if q=='ok':bins[i].append(v)
 points=[]
 for i,vs in enumerate(bins):
  q='ok' if vs else 'saturated' if 'saturated' in flags[i] else 'warmup' if 'warmup' in flags[i] else 'missing'
  points.append(dict(ts=start_ts+i*bucket_seconds,value=(max(vs) if sensor=='motion' else sum(vs)/len(vs)) if vs else None,quality=q,count=len(vs)))
 return wrap(dict(node=node,sensor=sensor,unit=units[sensor],bucket_seconds=bucket_seconds,aggregation='max' if sensor=='motion' else 'mean',points=points,truncated=False))
@app.get('/api/v1/sessions/{sid}/events')
@synchronized
def events(sid:str):
 s=tick(get(sid));engine=s.get('_engine')
 return wrap(sorted(engine.events.values(),key=lambda e:(-e['priority']['score'],-e['started_ts'])) if engine else [])

@app.get('/api/v1/sessions/{sid}/events/{eid}')
@synchronized
def event_detail(sid:str,eid:str):
 s=tick(get(sid));engine=s.get('_engine')
 if not engine or eid not in engine.events:raise HTTPException(404,'Event not found in current generation')
 return wrap(engine.events[eid])

class ReportBody(BaseModel):
 model_config=ConfigDict(extra='forbid')
 handling_status:str
 note:str=''
 expected_version:int

@app.patch('/api/v1/sessions/{sid}/events/{eid}')
@synchronized
def report(sid:str,eid:str,b:ReportBody):
 e=event_detail(sid,eid)['data']
 if b.expected_version!=e['version']:raise HTTPException(409,'VERSION_CONFLICT: reload event')
 allowed={'new':{'acknowledged','in_progress','resolved'},'acknowledged':{'in_progress','resolved'},'in_progress':{'resolved'},'resolved':set()}
 if b.handling_status not in allowed.get(e['handling_status'],set()):raise HTTPException(409,'Invalid handling transition')
 if len(b.note)>2000:raise HTTPException(422,'Note maximum is 2000 characters')
 now=time.time()
 e['reports'].append(dict(id=str(uuid.uuid4()),handling_status=b.handling_status,note=b.note,reported_at=now))
 e.update(handling_status=b.handling_status,version=e['version']+1,updated_at=now)
 persist([e]);return wrap(e)

@app.get('/api/v1/sessions/{sid}/stream')
def stream(sid:str):
 get(sid)
 async def generate():
  # Full snapshots; replay/reconnect uses REST re-fetch, not an event history buffer.
  while True:
   yield 'event: snapshot.updated\ndata: '+json.dumps(wrap(snapshot(get(sid))),ensure_ascii=False)+'\n\n'
   await asyncio.sleep(1)
 return StreamingResponse(generate(),media_type='text/event-stream',headers={'Cache-Control':'no-cache'})
