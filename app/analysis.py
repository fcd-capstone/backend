"""Opt-in, unvalidated rule engine. No learned thresholds or probabilities."""
from collections import deque
import copy, hashlib, json, math, operator, uuid
OPS={'gt':operator.gt,'ge':operator.ge,'lt':operator.lt,'le':operator.le,'eq':operator.eq}
SENSORS={'temp','eco2','tvoc','pm1','pm25','pm10','motion','co2','dp'}
CAUSES={'worker_activity','external_inflow','hvac_fault','equipment_fault','sensor_fault'}

def load_config(path):
 c=json.loads(path.read_text(encoding='utf-8'))
 if not isinstance(c.get('enabled'),bool) or not isinstance(c.get('version'),str):raise ValueError('Invalid rule configuration')
 w=c.get('window_seconds',60)
 if not isinstance(w,(int,float)) or not math.isfinite(w) or w<=0:raise ValueError('Invalid window')
 seen=set()
 for r in c.get('rules',[]):
  if r['id'] in seen or r['cause'] not in CAUSES:raise ValueError('Invalid/duplicate rule')
  seen.add(r['id'])
  for key in ('hold_seconds','recovery_seconds'):
   if not isinstance(r[key],(int,float)) or not math.isfinite(r[key]) or r[key]<0:raise ValueError('Invalid duration')
  if not r.get('conditions'):raise ValueError('Empty conditions')
  if not 0<=r['priority_score']<=100:raise ValueError('Invalid priority')
  for p in r['conditions']:
   if p['node'] not in ['node-a','node-b'] or p['sensor'] not in SENSORS:raise ValueError('Invalid sensor (humidity forbidden)')
   if p['node']=='node-b' and p['sensor'] in ['co2','dp']:raise ValueError('Sensor not installed')
   if p['feature'] not in ['value','delta','rate','missing_seconds'] or p['op'] not in OPS or not math.isfinite(p['threshold']):raise ValueError('Invalid condition')
 c['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
 return c

class Engine:
 def __init__(self,config,session_id,generation,start_ts):
  self.config=copy.deepcopy(config);self.session_id=session_id;self.generation=generation;self.start=start_ts
  self.history={};self.missing={};self.latest={};self.pending={};self.clear={};self.active={};self.events={}
  self.status='warming_up';self.decision='warming_up';self.last_ts=start_ts
 def feature(self,p,ts):
  k=(p['node'],p['sensor']);last=self.latest.get(k)
  if last is None or ts-last[0]>5:return None
  if p['feature']=='missing_seconds':return ts-self.missing[k] if k in self.missing else 0
  if last[1] is None:return None
  if p['feature']=='value':return last[1]
  h=self.history.get(k,[])
  if len(h)<2 or h[-1][0]-h[0][0]<self.config['window_seconds']*.8:return None
  delta=h[-1][1]-h[0][1]
  return delta if p['feature']=='delta' else delta/(h[-1][0]-h[0][0])
 def consume(self,raw,processed):
  ts=raw['ts'];self.last_ts=ts
  for sensor,v in processed['features'].items():
   if sensor not in SENSORS or (raw['node']=='node-b' and sensor in ['co2','dp']):continue
   k=(raw['node'],sensor);self.latest[k]=(ts,v)
   # Saturation is not a missing sensor fault. It remains unavailable to other rules.
   saturated=processed['quality'].get(sensor)=='saturated'
   if v is None and not saturated:self.missing.setdefault(k,ts)
   else:self.missing.pop(k,None)
   h=self.history.setdefault(k,deque())
   if v is not None:h.append((ts,v))
   while h and h[0][0]<ts-self.config['window_seconds']:h.popleft()
  if ts-self.start<self.config['window_seconds']:return []
  self.status='ready';changed=[];unknown=False
  for r in self.config['rules']:
   vals=[self.feature(p,ts) for p in r['conditions']]
   matched=all(v is not None and OPS[p['op']](v,p['threshold']) for p,v in zip(r['conditions'],vals))
   known=all(v is not None for v in vals);unknown|=not known
   rid=r['id']
   if matched:
    self.clear.pop(rid,None);self.pending.setdefault(rid,ts)
    if rid not in self.active and ts-self.pending[rid]>=r['hold_seconds']:
     score=r['priority_score'];eid=str(uuid.uuid4())
     ev=dict(id=eid,session_id=self.session_id,generation=self.generation,version=1,node_ids=sorted({p['node'] for p in r['conditions']}),cause=r['cause'],decision_status='anomaly',confidence=None,confidence_kind='none',method='rule',model_version=None,rule_id=rid,rule_version=self.config['version'],rule_sha256=self.config.get('sha256'),validation_status='unvalidated',started_ts=self.pending[rid],detected_ts=ts,ended_ts=None,lifecycle='active',end_reason=None,handling_status='new',priority={'score':score,'level':'low' if score<25 else 'medium' if score<50 else 'high' if score<75 else 'critical','basis':'configured_unvalidated','components':[]},evidence=[dict(sensor=p['sensor'],node=p['node'],feature=p['feature'],observed_value=v,unit=None,description=f"{p['node']} {p['sensor']} {p['feature']} {p['op']} {p['threshold']} (검증 전 설정)") for p,v in zip(r['conditions'],vals)],recommended_actions=r.get('recommended_actions',[]),reports=[],updated_at=ts)
     self.events[eid]=ev;self.active[rid]=eid;changed.append(ev)
   else:
    self.pending.pop(rid,None)
    if rid in self.active:
     if not known:self.clear.pop(rid,None);continue
     self.clear.setdefault(rid,ts)
     if ts-self.clear[rid]>=r['recovery_seconds']:
      ev=self.events[self.active.pop(rid)];ev.update(lifecycle='ended',ended_ts=ts,end_reason='recovered',version=ev['version']+1,updated_at=ts);changed.append(ev)
  self.decision='anomaly' if self.active else 'uncertain' if unknown else 'no_rule_match'
  return changed
 def close(self,ts,reason):
  changed=[]
  for eid in self.active.values():
   e=self.events[eid];e.update(lifecycle='ended',ended_ts=ts,end_reason=reason,version=e['version']+1,updated_at=ts);changed.append(e)
  self.active.clear();self.decision='uncertain';return changed
 def summary(self):
  active=[self.events[eid] for eid in self.active.values()]
  return dict(status=self.status,decision_status=self.decision,cause=active[0]['cause'] if len({e['cause'] for e in active})==1 else None,confidence=None,confidence_kind='none',method='rule',evidence=[p for e in active for p in e['evidence']],validation_status='unvalidated',rule_version=self.config['version'])
