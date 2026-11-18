import argparse,json
from .core import connect,ingest
p=argparse.ArgumentParser()
p.add_argument('csv');p.add_argument('--kind',choices=['sample','calibration','experiment'],required=True)
p.add_argument('--db',default='data/backend_v04.sqlite3')
def main():
    a=p.parse_args();db=connect(a.db)
    try: print(json.dumps(ingest(db,a.csv,a.kind),indent=2))
    finally: db.close()
if __name__=='__main__':main()
