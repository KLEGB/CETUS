import json,ijson,numpy as np,pandas as pd
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from test.paths import OUTPUT
import sys
root=Path(sys.argv[1] if len(sys.argv)>1 else OUTPUT)
a=root/'v84_matched/coupled';b=root/'shp_off/coupled'
x=pd.read_csv(a/'hourly.csv');y=pd.read_csv(b/'hourly.csv');common=set(x.columns)&set(y.columns)
columns={k:float(np.max(np.abs(x[k].to_numpy()-y[k].to_numpy()))) for k in common if pd.api.types.is_numeric_dtype(x[k])}
worst={};count=0
with (a/'building_hourly.json').open('rb') as f,(b/'building_hourly.json').open('rb') as g:
 for old,new in zip(ijson.items(f,'item',use_float=True),ijson.items(g,'item',use_float=True),strict=True):
  for u,v in zip(old,new,strict=True):
   count+=1
   for k,z in u.items():
    if k=='surfaces':
     for s,t in zip(z,v[k],strict=True):
      for field,value in s.items():
       if isinstance(value,(int,float)):worst['surface_'+field]=max(worst.get('surface_'+field,0),abs(value-t[field]))
    elif isinstance(z,(int,float)):worst[k]=max(worst.get(k,0),abs(z-v[k]))
old=json.loads((a/'state.json').read_text());new=json.loads((b/'state.json').read_text())
state=max(max(float(np.max(np.abs(np.array(u)-np.array(v)))) for u,v in zip(old['buildings'],new['buildings'],strict=True)),abs(old['air_c']-new['air_c']),float(np.max(np.abs(np.array(old['ground_c'])-new['ground_c']))))
result={'building_hours_compared':count,'site_hourly_max_abs_difference':columns,'building_max_abs_difference_by_field':worst,'max_final_state_difference_k':state,'optics':'Both tau_solar=.43*.7+.52*.2; control off; identical RAD cache'}
assert max(columns.values())<1e-7 and max(worst.values())<1e-5 and state<1e-7
(root/'v84_regression.json').write_text(json.dumps(result,indent=2));print(json.dumps({'building_hours':count,'site_max':max(columns.values()),'building_max':max(worst.values()),'state_max':state},indent=2))
