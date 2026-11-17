import unittest,tempfile,csv
from pathlib import Path
from app.core import *
ROOT=Path(__file__).resolve().parents[1]
class Tests(unittest.TestCase):
 def setUp(self):
  self.db=connect(':memory:');self.c=load_calibration(ROOT/'calib_params_v2.json')
  self.result=ingest(self.db,ROOT/'data/sample_5min.csv')
 def tearDown(self):self.db.close()
 def test_count(self):self.assertEqual(self.result['inserted'],654)
 def test_idempotent(self):
  r=ingest(self.db,ROOT/'data/sample_5min.csv');self.assertEqual(r['inserted'],0);self.assertEqual(r['duplicates'],654)
 def test_b(self):
  x=query(self.db,self.c,1,node='node-b',limit=1)[0];p=x['processed'];r=x['raw']
  self.assertIsNone(p['values']['humid']);self.assertIsNotNone(r['humid'])
  self.assertAlmostEqual(p['values']['temp'],r['temp']+.07)
  self.assertAlmostEqual(p['values']['eco2'],.9*r['eco2']+41.49)
  self.assertEqual(p['values']['pm1'],r['pm1']);self.assertEqual(p['quality']['co2'],'not_installed')
 def test_humidity_and_sample(self):
  p=query(self.db,self.c,1,node='node-a',limit=1)[0]['processed']
  self.assertNotIn('humid',p['features']);self.assertTrue(all(x is None for x in p['features'].values()))
 def test_warmup_and_saturation(self):
  r=query(self.db,self.c,1,limit=1)[0]['raw'];r['co2']=5000
  p=derive(r,self.c,'calibration',r['ts']);self.assertTrue(p['calibration_warmup']);self.assertEqual(p['quality']['co2'],'saturated')
  p=derive(r,self.c,'calibration',r['ts']-1800);self.assertFalse(p['calibration_warmup']);self.assertIsNone(p['features']['co2'])
 def test_conflict_rollback(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'bad.csv';r=query(self.db,self.c,1,limit=1)[0]['raw'];r['temp']+=1
   with p.open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerow(r)
   with self.assertRaises(ValueError):ingest(self.db,p)
   self.assertEqual(self.db.execute('SELECT COUNT(*) FROM datasets').fetchone()[0],1)
 def test_order(self):
  xs=query(self.db,self.c,1,limit=654);self.assertEqual([x['raw']['ts'] for x in xs],sorted(x['raw']['ts'] for x in xs))
if __name__=='__main__':unittest.main()
