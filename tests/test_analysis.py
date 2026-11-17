import json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.analysis import Engine,load_config
from app import server

def config(cause='equipment_fault'):
 return {'enabled':True,'version':'synthetic-test-only','window_seconds':2,'rules':[{'id':'test','cause':cause,'hold_seconds':1,'recovery_seconds':1,'priority_score':50,'conditions':[{'node':'node-a','sensor':'tvoc','feature':'value','op':'gt','threshold':10}],'recommended_actions':['테스트 조치']} ]}
def feed(e,t,v):
 return e.consume({'node':'node-a','ts':t},{'features':{'tvoc':v},'quality':{}})
class EngineTests(unittest.TestCase):
 def test_each_cause_and_merge_recover(self):
  # Tests rule mapping and lifecycle, NOT empirical cause discrimination.
  for cause in ['worker_activity','external_inflow','hvac_fault','equipment_fault','sensor_fault']:
   e=Engine(config(cause),'s',1,0)
   for t in range(6):feed(e,t,20)
   self.assertEqual(len(e.events),1);event=list(e.events.values())[0]
   self.assertEqual(event['cause'],cause);self.assertIsNone(event['confidence'])
   feed(e,6,0);feed(e,7,0)
   self.assertEqual(event['lifecycle'],'ended');self.assertEqual(event['end_reason'],'recovered')
 def test_unknown_not_recovered(self):
  e=Engine(config(),'s',1,0)
  for t in range(5):feed(e,t,20)
  for t in range(5,10):feed(e,t,None)
  self.assertEqual(len(e.active),1)
 def test_delta(self):
  c=config();c['rules'][0]['conditions'][0].update(feature='delta',threshold=1)
  e=Engine(c,'s',1,0)
  for t in range(6):feed(e,t,t*2)
  self.assertEqual(len(e.events),1)
 def test_config_rejects_humidity(self):
  c=config();c['rules'][0]['conditions'][0]['sensor']='humid'
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'r.json';p.write_text(json.dumps(c))
   with self.assertRaises(ValueError):load_config(p)
 def test_no_match_not_normal(self):
  e=Engine(config(),'s',1,0)
  for t in range(5):feed(e,t,0)
  self.assertEqual(e.summary()['decision_status'],'no_rule_match');self.assertIsNone(e.summary()['cause'])
 def test_http_reports_persistence_and_seek(self):
  with tempfile.TemporaryDirectory() as d,patch.object(server,'DB_PATH',Path(d)/'test.db'):
   with TestClient(server.app) as client:
    ds=client.get('/api/v1/datasets').json()['data'][0]
    with patch.object(server,'rules_config',return_value=config()):
     s=client.post('/api/v1/sessions',json={'dataset_id':ds['id']}).json()['data']
     state=server.sessions[s['id']]
     self.assertIsNone(state['_engine']) # sample is NEVER analyzed, even with rules enabled
     state['_kind']='experiment';server.setup_analysis(state) # synthetic injected fixture only
    e=state['_engine'];start=state['start_ts']
    for t in range(5):server.persist(feed(e,start+t,20))
    base='/api/v1/sessions/'+s['id'];events=client.get(base+'/events').json()['data'];self.assertEqual(len(events),1)
    event=events[0];url=base+'/events/'+event['id']
    body={'expected_version':1,'handling_status':'in_progress','note':'점검'}
    r=client.patch(url,json=body);self.assertEqual(r.status_code,200);self.assertEqual(r.json()['data']['version'],2)
    self.assertEqual(client.patch(url,json=body).status_code,409)
    db=server.database()
    try:self.assertEqual(json.loads(db.execute('SELECT payload FROM analysis_events').fetchone()[0])['handling_status'],'in_progress')
    finally:db.close()
    client.patch(base+'/replay',json={'action':'seek','ts':start})
    self.assertEqual(client.get(url).status_code,404)
   server.sessions.clear()
