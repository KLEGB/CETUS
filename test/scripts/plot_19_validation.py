from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from test.paths import OUTPUT
import json,numpy as np,pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import sys
r=Path(sys.argv[1] if len(sys.argv)>1 else OUTPUT);o=r/'figures';o.mkdir(exist_ok=True)
a=json.loads((r/'shp_on/summary.json').read_text());b=json.loads((r/'shp_off/summary.json').read_text())
plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False,'pdf.fonttype':42,'svg.fonttype':'none'})
x=pd.read_csv(r/'shp_on/coupled/hourly.csv');y=pd.read_csv(r/'shp_off/coupled/hourly.csv');h=np.arange(len(x))
f,axes=plt.subplots(3,1,figsize=(10,8),sharex=True,layout='constrained')
for ax,key,label in zip(axes,['lighting_electricity_w_m2_site','hvac_electricity_w_m2_site','hvac_plus_lighting_electricity_w_m2_site'],['Lighting (W/m² site)','HVAC (W/m² site)','HVAC + lighting (W/m² site)']):
 ax.plot(h,y[key],label='Daylight control off',color='#737b88',lw=1.8)
 ax.plot(h,x[key],label='300 lx switching',color='#bd3f45',lw=1.8);ax.set_ylabel(label);ax.grid(alpha=.2)
 for z in [24,48]:ax.axvline(z,color='black',lw=.6,alpha=.3)
axes[0].legend(ncol=2,frameon=False);axes[0].set_title('756 buildings · 72 hours · identical glazing and weather')
axes[-1].set_xlabel('Hour from start (HOY 5016)')
for ext in ['png','pdf','svg']:f.savefig(o/('lighting_hvac_72h.'+ext),dpi=170)
plt.close(f)
t=pd.read_csv(r/'ashrae/annual_internal_gains.csv');f,axes=plt.subplots(2,2,figsize=(10,7),layout='constrained')
for ax,key,label in zip(axes.flat,['heating_mwh','cooling_mwh','peak_heating_kw','peak_cooling_kw'],['Annual heating (MWh)','Annual cooling (MWh)','Peak heating (kW)','Peak cooling (kW)']):
 for i,row in t.iterrows():
  lo=row[key+'_reference_min'];hi=row[key+'_reference_max'];ax.vlines(i,lo,hi,color='#adb6c4',lw=12,alpha=.65)
 ax.scatter(range(len(t)),t[key],color='#bd3f45',zorder=3,label='CETUS 1.9')
 ax.set_xticks(range(len(t)),t['case'].astype(str));ax.set(xlabel='Case',ylabel=label);ax.grid(axis='y',alpha=.2)
axes[0,0].legend(frameon=False);f.suptitle('ASHRAE 140 internal-gain comparisons · 8760 h per case\nGrey bars: six official example programs; ranges are not acceptance limits',fontsize=12)
for ext in ['png','pdf','svg']:f.savefig(o/('ashrae_internal_gains_annual.'+ext),dpi=170)
plt.close(f)
