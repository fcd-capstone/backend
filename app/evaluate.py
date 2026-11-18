"""Offline pilot evaluation; no model is automatically enabled in replay."""
import csv,json,math
from bisect import bisect_left,bisect_right
from collections import Counter,defaultdict
from pathlib import Path
from statistics import mean, median,pstdev
from .core import parse,derive,load_calibration
from .labels import load_labels,EXPERIMENT
ROOT=Path(__file__).resolve().parents[1]

def evaluate(root=ROOT):
    cal=load_calibration(root/'calib_params_v3.json')
    runs=load_labels(root/'data')
    nodes=defaultdict(list)
    with (root/'data'/EXPERIMENT).open(newline='',encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            r=parse(row);nodes[r['node']].append(r)
    times={};ages={}
    for n,rows in nodes.items():
        rows.sort(key=lambda r:r['ts']); times[n]=[r['ts'] for r in rows]; ages[n]=[]
        prev=None
        for r in rows:
            if prev is None or r['boot']!=prev['boot'] or r['seq']<prev['seq']: since=r['ts']
            ages[n].append(r['ts']-since);prev=r
    vectors={};offsets={}
    for run in runs:
        start,end=run['start_ts'],run['end_ts'];rid=run['run']
        if start<min(t[0] for t in times.values()) or end>max(t[-1] for t in times.values()):raise ValueError(f'Run {rid} outside recording')
        if run['excluded_reason'] or run['source']=='pilot':continue
        v={};stable={}
        for n,rows in nodes.items():
            i,j=bisect_left(times[n],start),bisect_right(times[n],end)
            if i==j:raise ValueError(f'No {n} recording for run {rid}')
            series=defaultdict(list);stable[n]=defaultdict(list);motions=[]
            for raw,age in zip(rows[i:j],ages[n][i:j]):
                t=raw['ts']-start
                for k,x in derive(raw,cal,'experiment',start)['features'].items():
                    if x is None or (run['mask_pm'] and k.startswith('pm')) or (run['mask_a_motion'] and n=='node-a' and k=='motion'):continue
                    if k=='motion':motions.append((t,x));continue
                    if k=='dp' and t<10:continue
                    series[k].append((t,x))
                    if age>=43200 and k in ('eco2','tvoc'):stable[n][k].append(x)
            rises=[t for (_,a),(t,b) in zip(motions,motions[1:]) if a==0 and b==1]
            if motions:v[n+'.motion_rise']=float(bool(rises))
            for k,points in series.items():
                t0=points[0][0];base=median(x for t,x in points if t<=t0+5)
                xs=[x for _,x in points];ds=[b-a for a,b in zip(xs,xs[1:])]
                for suffix,value in [('delta',median(xs[-5:])-base),('excursion',max((x-base for x in xs),key=abs)),('spread',pstdev(xs)),('step',max(map(abs,ds),default=0))]:v[f'{n}.{k}.{suffix}']=value
                if k=='dp':v[n+'.dp.magnitude_median']=median(xs)
        if run['cause']=='normal':
            offsets[rid]={k:median(stable['node-a'][k])-median(stable['node-b'][k]) for k in ('eco2','tvoc') if stable['node-a'][k] and stable['node-b'][k]}
        vectors[rid]=(run['cause'],v)
    # Leave-one-run-out; all scaling and centroids are fit strictly on training runs.
    names=sorted({c for c,_ in vectors.values()});matrix={c:{d:0 for d in names} for c in names};predictions=[]
    for rid,(actual,test) in vectors.items():
        train=[(c,v) for i,(c,v) in vectors.items() if i!=rid];pool=defaultdict(list)
        for c,v in train:
            for k,x in v.items():pool[k].append(x)
        scales={k:pstdev(a) for k,a in pool.items()}; common=sorted(k for k in test if scales.get(k,0)>1e-9 and all(any(c==n and k in v for c,v in train) for n in names))
        def distance(n):
            return mean(((test[k]-mean(v[k] for c,v in train if c==n and k in v))/scales[k])**2 for k in common)
        predicted=min(names,key=distance);matrix[actual][predicted]+=1;predictions.append(dict(run=rid,actual=actual,predicted=predicted))
    pairs=Counter((x['actual'],x['predicted']) for x in predictions if x['actual']!=x['predicted'])
    return dict(method='leave-one-run-out / standardized nearest centroid, offline closed labeled windows',run_count=len(vectors),accuracy=sum(matrix[c][c] for c in names)/len(vectors),confusion_matrix=matrix,top_confusions=[dict(actual=a,predicted=b,count=n) for (a,b),n in pairs.most_common()],predictions=predictions,experimental_normal_alignment={k:{'eligible_runs':sum(k in x for x in offsets.values()),'candidate_B_offset_after_v3':median(a) if (a:=[x[k] for x in offsets.values() if k in x]) else None} for k in ('eco2','tvoc')},notes=['No pilot used in primary score. Candidate offsets are descriptive only, not used in classifier or automatically applied to display.','Only labeled windows; first 5 eligible seconds used as within-run reference, not true pre-event baseline. DP skips first 10s. Offline score does not measure streaming detection.','Masks use provided run annotations. Runs share the same chamber and two days; no claim of generalization.'])

if __name__=='__main__':print(json.dumps(evaluate(),ensure_ascii=False,indent=2))
