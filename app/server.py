"""Integration subset: historical CSV replay only; no fabricated analysis."""
import asyncio,json,time,uuid,math
from pathlib import Path
from fastapi import FastAPI,HTTPException,Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel,ConfigDict
from .core import connect,ingest,load_calibration,derive
ROOT=Path(__file__).resolve().parents[1]
app=FastAPI(title='FCD integration subset',version='0.2.0')
app.add_middleware(CORSMiddleware,allow_origins=['http://localhost:5173','http://127.0.0.1:5173'],allow_methods=['GET','POST','PATCH'],allow_headers=['Content-Type'])
sessions={}
units=dict(zip('temp humid eco2 tvoc pm1 pm25 pm10 motion co2 dp'.split(),['°C','%','ppm','ppb','µg/m³','µg/m³','µg/m³','0/1','ppm','Pa']))
def wrap(d):return {'data':d,'meta':{'request_id':str(uuid.uuid4())}}
def database():return connect(ROOT/'data/backend.sqlite3')
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
def health():return wrap({'status':'ok','version':'0.2.0','server_ts':time.time(),'capabilities':{'live':False,'replay':True,'analysis':False,'labels':False}})
@app.get('/api/v1/datasets')
def list_datasets():return wrap([{'id':str(d['id']),'name':d['name'],'kind':d['kind'],'start_ts':d['start_ts'],'end_ts':d['end_ts']} for d in datasets()])
class Create(BaseModel):
 model_config=ConfigDict(extra='forbid')
 mode:str='replay'
 dataset_id:str
 speed:float=1
class Control(BaseModel):
 model_config=ConfigDict(extra='forbid')
 action:str
 ts:float|None=None
 speed:float|None=None

def get(sid):
 if sid not in sessions:raise HTTPException(404,'Session not found (server restart clears sessions)')
 return sessions[sid]
def tick(s):
 if s['state']=='running':
  s['virtual_ts']=min(s['end_ts'],s['_anchor_ts']+(time.monotonic()-s['_anchor_clock'])*s['speed'])
  if s['virtual_ts']>=s['end_ts']:s['state']='completed'
 return s
def public(s):return {k:v for k,v in tick(s).items() if not k.startswith('_')}
@app.post('/api/v1/sessions',status_code=201)
def create(b:Create):
 if b.mode!='replay' or b.speed not in [.5,1,2,4,5,10]:raise HTTPException(422,'Only replay; supported speeds .5/1/2/4/5/10')
 d=next((d for d in datasets() if str(d['id'])==b.dataset_id),None)
 if not d:raise HTTPException(404,'Dataset not found')
 db=database()
 try:rows=[json.loads(r['raw']) for r in db.execute('SELECT m.raw FROM measurements m JOIN membership x ON m.node=x.node AND m.boot=x.boot AND m.seq=x.seq WHERE x.dataset_id=? ORDER BY m.ts,m.node',(d['id'],))]
 finally:db.close()
 sid=str(uuid.uuid4());now=time.time()
 s=dict(id=sid,mode='replay',dataset_id=b.dataset_id,state='paused',speed=b.speed,virtual_ts=d['start_ts'],start_ts=d['start_ts'],end_ts=d['end_ts'],generation=1,analysis_status='disabled',analysis_reason='analysis_not_implemented',created_at=now,updated_at=now,_rows=rows,_cal=load_calibration(ROOT/'calib_params_v2.json'),_kind=d['kind'],_anchor_ts=d['start_ts'],_anchor_clock=time.monotonic(),_from=d['start_ts'],_played=False)
 sessions[sid]=s;return wrap(public(s))
@app.get('/api/v1/sessions/{sid}')
def session(sid:str):return wrap(public(get(sid)))
@app.patch('/api/v1/sessions/{sid}/replay')
def control(sid:str,b:Control):
 s=tick(get(sid))
 if b.action not in ['play','pause','seek','stop','set_speed']:raise HTTPException(422,'Invalid action')
 if b.action=='seek':
  if b.speed is not None or b.ts is None or not math.isfinite(b.ts) or not s['start_ts']<=b.ts<=s['end_ts']:raise HTTPException(422,'Invalid seek')
  s.update(virtual_ts=b.ts,_from=b.ts,_played=False,generation=s['generation']+1,state='paused')
 elif b.action=='set_speed':
  if b.ts is not None or b.speed not in [.5,1,2,4,5,10]:raise HTTPException(422,'Invalid speed')
  if s['state'] not in ['running','paused']:raise HTTPException(409,'Seek before restart')
  s['speed']=b.speed
 else:
  if b.ts is not None or b.speed is not None:raise HTTPException(422,'Unexpected action fields')
  if b.action in ['play','pause'] and s['state'] in ['stopped','completed']:raise HTTPException(409,'Seek before restart')
  s['state']={'play':'running','pause':'paused','stop':'stopped'}[b.action]
  if b.action=='play':s['_played']=True
 s.update(_anchor_ts=s['virtual_ts'],_anchor_clock=time.monotonic(),updated_at=time.time())
 return wrap(public(s))
def eligible(s):
 if not s['_played']:return []
 return (r for r in s['_rows'] if s['_from']<=r['ts']<=s['virtual_ts'])
def snapshot(s):
 tick(s);last={}
 for r in eligible(s):last[r['node']]=r
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
 return dict(session_id=s['id'],generation=s['generation'],mode='replay',as_of_ts=s['virtual_ts'],nodes=nodes,analysis={'status':'disabled','decision_status':'disabled','cause':None,'confidence':None,'method':'none','evidence':[]},active_event_count=0)
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
 for r in eligible(s):
  if r['node']!=node or not start_ts<=r['ts']<=end_ts:continue
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
def events(sid:str):get(sid);return wrap([])
@app.get('/api/v1/sessions/{sid}/stream')
def stream(sid:str):
 get(sid)
 async def generate():
  # Full snapshots; replay/reconnect uses REST re-fetch, not an event history buffer.
  while True:
   yield 'event: snapshot.updated\ndata: '+json.dumps(wrap(snapshot(get(sid))),ensure_ascii=False)+'\n\n'
   await asyncio.sleep(1)
 return StreamingResponse(generate(),media_type='text/event-stream',headers={'Cache-Control':'no-cache'})
