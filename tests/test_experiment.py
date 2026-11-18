import csv,tempfile,unittest
from pathlib import Path
from app.core import FIELDS,connect,ingest,load_calibration,derive
from app.labels import load_labels
ROOT=Path(__file__).resolve().parents[1]
class ExperimentTests(unittest.TestCase):
 def test_restart_identity(self):
  with tempfile.TemporaryDirectory() as tmp, connect(':memory:') as db:
   p=Path(tmp)/'test.csv'
   with p.open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader()
    for ts in (1,2,2):w.writerow(dict(node='node-b',boot=1,seq=0,ts=ts))
   result=ingest(db,p,'experiment');self.assertEqual((result['inserted'],result['duplicates']),(2,1))
 def test_dp_and_motion(self):
  cal=load_calibration(ROOT/'calib_params_v3.json');r=dict.fromkeys(FIELDS)
  r.update(node='node-a',boot=1,seq=0,ts=1794901679,dp=-3.3,motion=0)
  p=derive(r,cal,'experiment',0);self.assertEqual(p['values']['dp'],-3.3);self.assertEqual(p['features']['dp'],3.3);self.assertIsNone(p['values']['motion'])
 def test_label_exclusions(self):
  if not (ROOT/'data/labels_day1.csv').exists():self.skipTest('private label fixtures not installed')
  runs=load_labels(ROOT/'data');self.assertEqual(sum(r['source']!='pilot' and not r['excluded_reason'] for r in runs),70)
  self.assertTrue(next(r['mask_pm'] for r in runs if r['run']==48))
