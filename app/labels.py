import csv, math
from collections import defaultdict
CAUSES={'normal','external_inflow','worker_activity','equipment_fault','sensor_fault','hvac_fault'}
EXPERIMENT='data_20261117_164803_fixed.csv'
def load_labels(folder):
    runs=defaultdict(dict)
    for source in ('pilot','day1','day2'):
        with (folder/f'labels_{source}.csv').open(encoding='utf-8-sig',newline='') as f:
            for row in csv.DictReader(f):
                run=int(row['run']);event=row['event'];ts=float(row['ts'])
                if event not in ('start','end') or event in runs[run] or not math.isfinite(ts) or row['cause'] not in CAUSES:
                    raise ValueError(f'Invalid label run {run}')
                runs[run][event]=dict(row,ts=ts,source=source)
    out=[]
    for run,p in sorted(runs.items()):
        if set(p)!={'start','end'}:raise ValueError(f'Unpaired run {run}')
        a,b=p['start'],p['end']
        if a['ts']>=b['ts'] or a['cause']!=b['cause'] or a['source']!=b['source']:raise ValueError(f'Invalid run {run}')
        reason='cancelled' if 'CANCEL' in b['note'].upper() else 'pilot_timing_or_motion' if run in (2,5,6) else None
        out.append(dict(run=run,cause=a['cause'],start_ts=a['ts'],end_ts=b['ts'],source=a['source'],note=a['note'],end_note=b['note'],target_node=a['target_node'] or None,intensity=a['intensity'] or None,excluded_reason=reason,mask_pm=run==48,mask_a_motion=run==79))
    return out
