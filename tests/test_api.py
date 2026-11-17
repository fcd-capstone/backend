"""HTTP contract regression tests. No listening server or production DB needed."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import server

class APIContractTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  self.old_db=server.DB_PATH
  server.DB_PATH=Path(self.tmp.name)/'isolated.sqlite3'
  server.sessions.clear()
  self.context=TestClient(server.app)
  self.c=self.context.__enter__()
  self.ds=self.c.get('/api/v1/datasets').json()['data'][0]
  self.s=self.c.post('/api/v1/sessions',json={'mode':'replay','dataset_id':self.ds['id'],'speed':1}).json()['data']
  self.base='/api/v1/sessions/'+self.s['id']
 def tearDown(self):
  self.context.__exit__(None,None,None)
  server.sessions.clear();server.DB_PATH=self.old_db;self.tmp.cleanup()
 def ctl(self,**body):return self.c.patch(self.base+'/replay',json=body)
 def snapshot(self):return self.c.get(self.base+'/snapshot').json()['data']
 def advance(self,seconds):
  state=server.sessions[self.s['id']]
  with patch.object(server.time,'monotonic',return_value=state['_anchor_clock']+seconds):
   return self.ctl(action='pause')
 def test_frontend_required_fields(self):
  for k in ['id','dataset_id','mode','state','speed','virtual_ts','start_ts','end_ts','generation','analysis_status','analysis_reason']:self.assertIn(k,self.s)
  p=self.snapshot()
  self.assertEqual(p['analysis']['status'],'disabled')
  for n in ['node-a','node-b']:
   self.assertEqual(p['nodes'][n]['node'],n)
   self.assertEqual(set(p['nodes'][n]['metrics']),set(server.units))
  self.assertEqual(p['nodes']['node-b']['metrics']['humid']['quality'],'disabled')
  self.assertEqual(p['nodes']['node-b']['metrics']['co2']['quality'],'not_installed')
 def test_pause_and_speed(self):
  self.ctl(action='play');self.advance(5)
  t=self.c.get(self.base).json()['data']['virtual_ts']
  self.assertAlmostEqual(t,self.s['start_ts']+5,places=4)
  self.assertEqual(self.c.get(self.base).json()['data']['virtual_ts'],t)
  self.assertEqual(self.ctl(action='set_speed',speed=4).status_code,200)
 def test_seek_clears_snapshot_and_series(self):
  self.ctl(action='play');self.advance(10)
  self.assertIsNotNone(self.snapshot()['nodes']['node-a']['metrics']['temp']['value'])
  s=self.ctl(action='seek',ts=self.s['start_ts']+5).json()['data']
  self.assertEqual(s['generation'],2);self.assertEqual(s['state'],'paused')
  self.assertIsNone(self.snapshot()['nodes']['node-a']['metrics']['temp']['value'])
  points=self.c.get(self.base+'/series',params={'node':'node-a','sensor':'temp','start_ts':self.s['start_ts'],'end_ts':s['virtual_ts']}).json()['data']['points']
  self.assertTrue(all(p['value'] is None for p in points))
 def test_b_calibration_and_humidity(self):
  self.ctl(action='play');self.advance(3)
  st=server.sessions[self.s['id']];raw=server.window(st,'node-b',st['start_ts'],st['virtual_ts'])[-1]
  b=self.snapshot()['nodes']['node-b']['metrics']
  self.assertIsNone(b['humid']['value']);self.assertAlmostEqual(b['temp']['value'],raw['temp']+.07)
  self.assertAlmostEqual(b['eco2']['value'],.9*raw['eco2']+41.49)
 def test_invalid_controls(self):
  for b in [{'action':'seek','ts':-1},{'action':'play','speed':2},{'action':'set_speed','speed':3},{'action':'unknown'}]:
   self.assertEqual(self.ctl(**b).status_code,422)
  self.ctl(action='stop');self.assertEqual(self.ctl(action='play').status_code,409)
  self.assertEqual(self.c.get('/api/v1/sessions/missing').status_code,404)
 def test_series_restrictions(self):
  params={'node':'node-a','sensor':'humid','start_ts':self.s['start_ts'],'end_ts':self.s['start_ts']}
  self.assertEqual(self.c.get(self.base+'/series',params=params).status_code,422)
  params.update(node='node-b',sensor='co2');self.assertEqual(self.c.get(self.base+'/series',params=params).status_code,422)
  params.update(node='node-a',sensor='temp',end_ts=self.s['end_ts']);self.assertEqual(self.c.get(self.base+'/series',params=params).status_code,422)
 def test_cors(self):
  r=self.c.options('/api/v1/sessions',headers={'Origin':'http://localhost:5173','Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'content-type'})
  self.assertEqual(r.status_code,200);self.assertEqual(r.headers['access-control-allow-origin'],'http://localhost:5173')
  r=self.c.options('/api/v1/sessions',headers={'Origin':'https://not-allowed.example','Access-Control-Request-Method':'POST'})
  self.assertEqual(r.status_code,400)
 def test_sse_frame(self):
  async def first():
   gen=server.stream(self.s['id']).body_iterator
   try:return await anext(gen)
   finally:await gen.aclose()
  frame=asyncio.run(first());self.assertIn('event: snapshot.updated',frame)
  payload=json.loads(frame.split('data: ',1)[1]);self.assertEqual(payload['data']['session_id'],self.s['id']);self.assertIn('meta',payload)

if __name__=='__main__':unittest.main()
