"""CSV contract, immutable raw storage and versioned derived views."""
import csv, json, math, sqlite3, hashlib
from pathlib import Path
FIELDS = 'node boot seq ts recv_ts temp humid eco2 tvoc pm1 pm25 pm10 motion co2 dp'.split()
SENSORS = FIELDS[5:]

def connect(path):
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    db.executescript('''
    CREATE TABLE IF NOT EXISTS measurements (
      node TEXT NOT NULL, boot INTEGER NOT NULL, seq INTEGER NOT NULL,
      ts REAL NOT NULL, raw TEXT NOT NULL,
      PRIMARY KEY(node,boot,seq));
    CREATE INDEX IF NOT EXISTS measurement_time ON measurements(node,ts);
    CREATE TABLE IF NOT EXISTS datasets (
      id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
      start_ts REAL NOT NULL, end_ts REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS membership (
      dataset_id INTEGER NOT NULL, node TEXT NOT NULL, boot INTEGER NOT NULL, seq INTEGER NOT NULL,
      PRIMARY KEY(dataset_id,node,boot,seq));
    ''')
    return db

def parse(row):
    if set(row) != set(FIELDS):
        raise ValueError('CSV must contain the specified 15 columns')
    if row['node'] not in ('node-a','node-b'):
        raise ValueError('Unknown node')
    result={'node':row['node']}
    for key in FIELDS[1:]:
        value=row[key]
        if value is None or str(value).strip()=='':
            if key in ('boot','seq','ts'):
                raise ValueError(f'{key} is required')
            result[key]=None
            continue
        value=float(value)
        if not math.isfinite(value):
            raise ValueError(f'{key} must be finite')
        if key in ('boot','seq','eco2','tvoc','pm1','pm25','pm10','motion','co2'):
            if not value.is_integer():
                raise ValueError(f'{key} must be integer')
            value=int(value)
        result[key]=value
    if result['seq']<1 or result['boot']<0 or result['ts']<0:
        raise ValueError('Invalid identity/time')
    if result['motion'] not in (None,0,1):
        raise ValueError('motion must be 0, 1 or null')
    return result

def ingest(db,path,kind='sample'):
    if kind not in ('sample','calibration','experiment'):
        raise ValueError('Invalid dataset kind')
    inserted=duplicates=0
    # Transaction rolls back the entire import on malformed/conflicting input.
    with db:
        cur=db.execute('INSERT INTO datasets(name,kind,start_ts,end_ts) VALUES (?,?,0,0)',(Path(path).name,kind))
        dataset=cur.lastrowid
        lo,hi=float('inf'),float('-inf')
        with open(path,encoding='utf-8-sig',newline='') as f:
            reader=csv.DictReader(f)
            if reader.fieldnames != FIELDS:
                raise ValueError('CSV column order does not match contract')
            for line,row in enumerate(reader,2):
                try:
                    r=parse(row)
                    key=(r['node'],r['boot'],r['seq'])
                    payload=json.dumps(r,sort_keys=True,allow_nan=False)
                    prev=db.execute('SELECT raw FROM measurements WHERE node=? AND boot=? AND seq=?',key).fetchone()
                    if prev:
                        # recv_ts may differ on retransmission; never overwrite original.
                        old=json.loads(prev['raw'])
                        if any(old[k]!=r[k] for k in FIELDS if k!='recv_ts'):
                            raise ValueError('Same identity with conflicting measurement')
                        duplicates+=1
                    else:
                        db.execute('INSERT INTO measurements VALUES (?,?,?,?,?)',(*key,r['ts'],payload))
                        inserted+=1
                    db.execute('INSERT OR IGNORE INTO membership VALUES (?,?,?,?)',(dataset,*key))
                    lo=min(lo,r['ts']);hi=max(hi,r['ts'])
                except (ValueError,TypeError) as e:
                    raise ValueError(f'CSV line {line}: {e}') from e
        if not math.isfinite(lo):
            raise ValueError('Empty CSV')
        db.execute('UPDATE datasets SET start_ts=?,end_ts=? WHERE id=?',(lo,hi,dataset))
    return dict(dataset_id=dataset,inserted=inserted,duplicates=duplicates,start_ts=lo,end_ts=hi)

def load_calibration(path):
    c=json.loads(Path(path).read_text(encoding='utf-8'))
    if c['reference']!='node-a' or c['target']!='node-b' or 'humid' in c['params']:
        raise ValueError('Invalid calibration contract')
    for sensor,p in c['params'].items():
        if sensor not in SENSORS or not all(math.isfinite(float(p[k])) for k in ('slope','intercept')):
            raise ValueError('Invalid calibration parameter')
    c['sha256']=hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return c

def derive(raw,calibration,kind,start_ts):
    values={k:raw[k] for k in SENSORS}
    quality={}
    if raw['node']=='node-b':
        values['humid']=None
        quality['humid']='disabled_known_fault'
        for k in ('co2','dp'):
            values[k]=None;quality[k]='not_installed'
        for k,p in calibration['params'].items():
            if values[k] is not None:
                values[k]=p['slope']*values[k]+p['intercept']
    for k,v in values.items():
        if v is None and k not in quality:
            quality[k]='missing'
    saturated=raw['node']=='node-a' and raw['co2'] is not None and raw['co2']>=5000
    if saturated: quality['co2']='saturated'
    warmup=kind=='calibration' and raw['ts']<start_ts+1800
    # Humidity is display-only for A, never a model feature for either node.
    features={k:v for k,v in values.items() if k!='humid'}
    if saturated: features['co2']=None
    if warmup or kind=='sample':
        features={k:None for k in features}
    return dict(node=raw['node'],boot=raw['boot'],seq=raw['seq'],ts=raw['ts'],
                values=values,quality=quality,features=features,
                calibration_warmup=warmup,training_eligible=kind=='experiment',
                baseline_eligible=False,
                calibration_version=calibration['version'],calibration_sha256=calibration['sha256'],
                dp_correction_status='pending_sign_confirmation' if raw['node']=='node-a' else 'not_installed')

def query(db,calibration,dataset_id,node=None,start=None,end=None,limit=1000,offset=0,latest=False):
    ds=db.execute('SELECT * FROM datasets WHERE id=?',(dataset_id,)).fetchone()
    if ds is None: raise ValueError('Dataset not found')
    sql='''SELECT m.raw FROM measurements m JOIN membership x
    ON m.node=x.node AND m.boot=x.boot AND m.seq=x.seq WHERE x.dataset_id=?'''
    args=[dataset_id]
    for clause,value in [('m.node=?',node),('m.ts>=?',start),('m.ts<=?',end)]:
        if value is not None: sql+=' AND '+clause;args.append(value)
    sql+=' ORDER BY m.ts '+('DESC' if latest else 'ASC')+', m.node, m.boot, m.seq LIMIT ? OFFSET ?'
    rows=db.execute(sql,(*args,limit,offset)).fetchall()
    return [dict(raw=json.loads(r['raw']),processed=derive(json.loads(r['raw']),calibration,ds['kind'],ds['start_ts'])) for r in rows]
