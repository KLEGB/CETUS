"""Summarize completed V9.1 material scenarios, preserving reflection modes."""
import argparse,json,csv
from pathlib import Path
import numpy as np
p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args()
out=a.root/'91_comparison';out.mkdir(exist_ok=True)
names=['matched_one','selective_one','matched_full','selective_full','broadband_high']
summary={name:json.loads((a.root/('91_'+name)/'summary.json').read_text()) for name in names}
rows=[]
for name,s in summary.items():
 rows.append(dict(scenario=name,mean_uhi_k=s['mean_uhi_k'],roof_mean_c=s['roof_mean_c'],hvac_kwh=s['lighting']['hvac_electricity_kwh'],lighting_kwh=s['lighting']['lighting_electricity_kwh'],total_hvac_lighting_kwh=s['lighting']['hvac_plus_lighting_electricity_kwh'],building_net_lw_out_kwh=s['longwave_building_net_out_kwh'],space_escape_kwh=s['space_escape_kwh'],unresolved_reflection_kwh=s['second_reflection_unresolved_kwh'],max_site_balance_w_m2=s['max_site_balance_w_m2'],max_building_balance_w=s['max_building_balance_w'],**s['spectral']))
with (out/'summary.csv').open('w') as f:
 w=csv.DictWriter(f,fieldnames=rows[0]);w.writeheader();w.writerows(rows)
result={'scenarios':rows,'deltas':{},'truncation_impact':{}}
for mode in ['one','full']:
 n=summary['matched_'+mode];s=summary['selective_'+mode]
 result['deltas'][mode]=dict(uhi_reduction_k=n['mean_uhi_k']-s['mean_uhi_k'],roof_reduction_k=n['roof_mean_c']-s['roof_mean_c'],hvac_saving_kwh=n['lighting']['hvac_electricity_kwh']-s['lighting']['hvac_electricity_kwh'],hvac_saving_pct=100*(1-s['lighting']['hvac_electricity_kwh']/n['lighting']['hvac_electricity_kwh']),lighting_difference_kwh=n['lighting']['lighting_electricity_kwh']-s['lighting']['lighting_electricity_kwh'])
for material in ['matched','selective']:
 one=summary[material+'_one'];full=summary[material+'_full']
 result['truncation_impact'][material]=dict(uhi_one_minus_full_k=one['mean_uhi_k']-full['mean_uhi_k'],roof_one_minus_full_k=one['roof_mean_c']-full['roof_mean_c'],hvac_one_minus_full_kwh=one['lighting']['hvac_electricity_kwh']-full['lighting']['hvac_electricity_kwh'])
result['high_grey_reference']=dict(uhi_selective_minus_high_grey_k=summary['selective_full']['mean_uhi_k']-summary['broadband_high']['mean_uhi_k'],roof_selective_minus_high_grey_k=summary['selective_full']['roof_mean_c']-summary['broadband_high']['roof_mean_c'],hvac_selective_minus_high_grey_kwh=summary['selective_full']['lighting']['hvac_electricity_kwh']-summary['broadband_high']['lighting']['hvac_electricity_kwh'])
hourlies={}
for name in names:
 with (a.root/('91_'+name)/'coupled/hourly.csv').open() as f:hourlies[name]=list(csv.DictReader(f))
hr=[]
for i in range(72):
 r={'hoy':int(hourlies[names[0]][i]['hoy'])}
 for name in names:
  x=hourlies[name][i]
  for key in ['Tair','Tambient','Tsurface','TgGround','hvac_electricity_w_m2_site','building_net_lw_w','lw_second_reflection_unresolved_w']:
   r[name+'_'+key]=float(x[key])
  r[name+'_uhi_k']=float(x['Tair'])-float(x['Tambient'])
 hr.append(r)
with (out/'hourly_comparison.csv').open('w') as f:
 w=csv.DictWriter(f,fieldnames=hr[0]);w.writeheader();w.writerows(hr)
(out/'comparison.json').write_text(json.dumps(result,indent=2))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.rcParams.update({'font.size':10,'svg.fonttype':'none','pdf.fonttype':42})
fig,ax=plt.subplots(2,2,figsize=(11,7.5),layout='constrained')
colors={'matched_one':'#8c959f','selective_one':'#ba383b','matched_full':'#536a83','selective_full':'#db7454','broadband_high':'#3c8971'}
labels={'matched_one':'Matched grey: one bounce','selective_one':'Selective: one bounce','matched_full':'Matched grey: full reflection','selective_full':'Selective: full reflection','broadband_high':'Grey 0.9: full reflection'}
for name in names:
 ax[0,0].plot(range(72),[r[name+'_uhi_k'] for r in hr],label=labels[name],color=colors[name],linestyle='--' if name.endswith('one') else '-')
ax[0,0].set(xlabel='Hour from start',ylabel='UHI (K)',title='(a) Canopy temperature response');ax[0,0].legend(frameon=False,fontsize=8)
ax[0,1].bar(range(5),[summary[n]['lighting']['hvac_electricity_kwh']/1000 for n in names],color=[colors[n] for n in names])
ax[0,1].set(xticks=range(5),xticklabels=['Grey\n1 bounce','Selective\n1 bounce','Grey\nfull','Selective\nfull','Grey 0.9\nfull'],ylabel='HVAC electricity (MWh)',title='(b) 72-hour HVAC electricity')
plate=json.loads((a.root/'91_plate/plate.json').read_text())
for name,color in [('matched_grey','#536a83'),('selective','#ba383b'),('high_grey','#3c8971')]:
 for solar,style in [(0,'-'),(200,'--')]:
  rr=[x for x in plate if x['material']==name and x['absorbed_shortwave_w_m2']==solar]
  ax[1,0].plot([x['window_atmospheric_transmittance'] for x in rr],[x['equilibrium_surface_c'] for x in rr],color=color,linestyle=style,label=name+f', absorbed SW={solar:g}')
ax[1,0].set(xlabel='8–13 µm atmospheric transmittance',ylabel='Plate temperature (°C)',title='(c) Independent isolated plate');ax[1,0].legend(fontsize=7,frameon=False)
for mode,color in [('one','#8c959f'),('full','#ba383b')]:
 ax[1,1].plot(range(72),[r['matched_'+mode+'_uhi_k']-r['selective_'+mode+'_uhi_k'] for r in hr],color=color,label='One bounce' if mode=='one' else 'Full reflection')
ax[1,1].set(xlabel='Hour from start',ylabel='UHI reduction (K)',title='(d) Spectral selectivity at matched 300 K emission');ax[1,1].legend(frameon=False)
for x in ax.flat:x.spines[['top','right']].set_visible(False);x.grid(axis='y',alpha=.2);x.set_axisbelow(True)
for ext in ['png','svg','pdf']:fig.savefig(out/('longwave_comparison.'+ext),dpi=180)
plt.close(fig)
print(json.dumps(result,indent=2))
