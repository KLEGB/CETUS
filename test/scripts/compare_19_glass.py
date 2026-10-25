"""Summarize four completed matched-solar glass/control SHP runs.
Usage: python test/scripts/compare_19_glass.py --root ../outputs
Generate CSV and figures; the detailed interpretation accompanies the results.
"""
import argparse,csv,json
from pathlib import Path
import numpy as np
p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args()
out=a.root/'glass_comparison';out.mkdir(exist_ok=True)
names={'normal_off':'glass_normal_off','selective_off':'shp_off',
       'normal_on':'glass_normal_on','selective_on':'shp_on'}
summaries={k:json.loads((a.root/v/'summary.json').read_text()) for k,v in names.items()}
keys=['lighting_electricity_kwh','hvac_electricity_kwh','hvac_plus_lighting_electricity_kwh']
rows=[dict(scenario=k,**{x:s['lighting'][x] for x in keys},
           max_site_balance_w_m2=s['max_site_balance_w_m2'],max_building_balance_w=s['max_building_balance_w'])
      for k,s in summaries.items()]
with (out/'energy_comparison.csv').open('w') as f:
 w=csv.DictWriter(f,fieldnames=rows[0]);w.writeheader();w.writerows(rows)
results={'energy':rows,'deltas':{}}
for mode in ['on','off']:
 n=summaries['normal_'+mode]['lighting'];s=summaries['selective_'+mode]['lighting']
 results['deltas'][mode]={k:dict(normal=n[k],selective=s[k],saving_kwh=n[k]-s[k],saving_pct=100*(n[k]-s[k])/n[k]) for k in keys}
# Validate daylight groups are matched and record threshold crossings.
with (a.root/names['normal_on']/'coupled/daylighting_hourly.csv').open() as f:normal=list(csv.DictReader(f))
with (a.root/names['selective_on']/'coupled/daylighting_hourly.csv').open() as f:selective=list(csv.DictReader(f))
assert len(normal)==len(selective)
changed=0;changed_area_hours=0.;max_lux_ratio_error=0.;ratio=.7/(.405/.95)
hour_cross={}
for n,s in zip(normal,selective):
 assert all(n[k]==s[k] for k in ('building_index','hoy','group','area_m2'))
 nv,sv=float(n['daylight_lux']),float(s['daylight_lux'])
 max_lux_ratio_error=max(max_lux_ratio_error,abs(sv-ratio*nv))
 assert int(s['calls_for_light'])<=int(n['calls_for_light'])
 if int(n['calls_for_light'])>int(s['calls_for_light']):
  changed+=1;changed_area_hours+=float(n['area_m2'])
  hour_cross[int(n['hoy'])]=hour_cross.get(int(n['hoy']),0.)+float(n['area_m2'])
results['daylight']=dict(matched_group_hours=len(normal),threshold_crossed_group_hours=changed,
 threshold_crossed_area_hours_m2h=changed_area_hours,lux_ratio=ratio,max_lux_scaling_error_lux=max_lux_ratio_error,
 normal_equivalent_threshold_for_selective_lux=300/ratio)
# Compare all numeric fields in no-control hourly outputs.
with (a.root/names['normal_off']/'coupled/hourly.csv').open() as f:n=list(csv.DictReader(f))
with (a.root/names['selective_off']/'coupled/hourly.csv').open() as f:s=list(csv.DictReader(f))
assert len(n)==len(s)
results['off_hourly_max_abs_difference']={k:max(abs(float(x[k])-float(y[k])) for x,y in zip(n,s)) for k in n[0]}
# Audit total thermal solar transmission and cooling/heating loads independently.
import ijson
results['thermal_audit']={}
for label,folder in names.items():
 totals=dict(transmitted_solar_kwh=0.,heating_load_kwh=0.,cooling_load_kwh=0.)
 with (a.root/folder/'coupled/building_hourly.json').open('rb') as f:
  for b in ijson.items(f,'item',use_float=True):
   for r in b:
    totals['transmitted_solar_kwh']+=sum(s['transmitted_w'] for s in r['surfaces'])/1000
    totals['heating_load_kwh']+=max(0.,r['hvac_w'])/1000
    totals['cooling_load_kwh']+=max(0.,-r['hvac_w'])/1000
 results['thermal_audit'][label]=totals
# Export matched hourly series (site densities preserve exact emitted units).
hourly=[]
with (a.root/names['normal_on']/'coupled/hourly.csv').open() as f:n=list(csv.DictReader(f))
with (a.root/names['selective_on']/'coupled/hourly.csv').open() as f:s=list(csv.DictReader(f))
for x,y in zip(n,s):
 assert x['hoy']==y['hoy']
 r={'hoy':int(x['hoy']),'threshold_crossed_area_m2':hour_cross.get(int(x['hoy']),0.)}
 for key in ['lighting_electricity_w_m2_site','hvac_electricity_w_m2_site','hvac_plus_lighting_electricity_w_m2_site','Tair']:
  r['normal_'+key]=float(x[key]);r['selective_'+key]=float(y[key])
  r['normal_minus_selective_'+key]=float(x[key])-float(y[key])
 hourly.append(r)
with (out/'hourly_comparison.csv').open('w') as f:
 w=csv.DictWriter(f,fieldnames=hourly[0]);w.writeheader();w.writerows(hourly)
(out/'comparison.json').write_text(json.dumps(results,indent=2))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.rcParams.update({'font.size':10,'svg.fonttype':'none','pdf.fonttype':42})
fig,axes=plt.subplots(1,2,figsize=(11,4),layout='constrained')
colors=['#949ba5','#ba383b'];xs=np.arange(3)
for j,case in enumerate(['normal_on','selective_on']):
 axes[0].bar(xs+(j-.5)*.34,[summaries[case]['lighting'][k]/1000 for k in keys],width=.34,
             label=['Normal glass','Selective glass'][j],color=colors[j])
axes[0].set_xticks(xs,['Lighting','HVAC','HVAC + lighting']);axes[0].set_ylabel('72-hour electricity (MWh)');axes[0].legend(frameon=False)
axes[0].set_title('(a) Equal solar transmittance = 0.405')
for j,case in enumerate(['normal','selective']):
 axes[1].plot([r['hoy']-hourly[0]['hoy'] for r in hourly],
 [r[case+'_lighting_electricity_w_m2_site'] for r in hourly],color=colors[j],label=['Normal glass','Selective glass'][j])
axes[1].set_xlabel('Hour from start');axes[1].set_ylabel('Lighting power (W/m² site)');axes[1].set_title('(b) 300 lx switching control')
for ax in axes:
 ax.spines[['top','right']].set_visible(False);ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True)
for ext in ['png','svg','pdf']:fig.savefig(out/f'glass_comparison.{ext}',dpi=180)
plt.close(fig)
print(json.dumps(results,indent=2))
