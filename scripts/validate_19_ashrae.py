"""Annual ASHRAE 140 internal-gain comparisons, plus recovered July regression.

Inputs independently constructed from ANL-20/26 normative specifications;
no informative participant IDFs are translated or used to build these cases.
Constant convective films are the permitted alternative; nonlinear IR is solved.
Solar is isotropic diffuse + NOAA midpoint beam, a declared approximation.
Requires --standard-files pointing at the official extracted TF folder.
"""
import argparse,sys,json,copy,gzip,math
from pathlib import Path
import numpy as np
import pandas as pd
import openpyxl
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ENERGY.surface import SurfaceEngine,SIGMA
p=argparse.ArgumentParser();p.add_argument('--standard-files',type=Path,required=True)
p.add_argument('--recovered',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
weather=pd.read_csv(next(a.standard_files.rglob('725650TY.csv')),skiprows=1)
assert len(weather)==8760
T=weather['Dry-bulb (C)'].to_numpy(float)
w=openpyxl.load_workbook(next(a.standard_files.rglob('Tsky-Informative.xlsx')),data_only=True,read_only=True)
Ts=np.array([row[3] for row in list(w['Informative-Tsky'].values)[4:8764]],float);assert len(Ts)==8760
# Plane irradiation in N/E/S/W order; no solar enters high-conductance opaque panels.
doy=np.arange(8760)//24+1;hour=np.arange(8760)%24+.5
z=2*np.pi/365*(doy-1+(hour-12)/24)
decl=.006918-.399912*np.cos(z)+.070257*np.sin(z)-.006758*np.cos(2*z)+.000907*np.sin(2*z)-.002697*np.cos(3*z)+.00148*np.sin(3*z)
eot=229.18*(.000075+.001868*np.cos(z)-.032077*np.sin(z)-.014615*np.cos(2*z)-.040849*np.sin(2*z))
angle=np.deg2rad((hour*60+eot+4*(-104.65)-60*(-7))/4-180);lat=np.deg2rad(39.833)
sun=np.column_stack((-np.cos(decl)*np.sin(angle),np.cos(lat)*np.sin(decl)-np.sin(lat)*np.cos(decl)*np.cos(angle),np.sin(lat)*np.sin(decl)+np.cos(lat)*np.cos(decl)*np.cos(angle)))
ghi=weather['GHI (W/m^2)'].to_numpy(float);dhi=weather['DHI (W/m^2)'].to_numpy(float);dni=weather['DNI (W/m^2)'].to_numpy(float)
beam=dni*(sun[:,2]>0)
planes=np.maximum(sun@np.array([[0,1,0],[1,0,0],[0,-1,0],[-1,0,0]]).T,0)*beam[:,None]+.5*dhi[:,None]+.1*ghi[:,None]

def layer(thickness,k,rho,cp):return {'resistance_m2k_w':thickness/k,'capacity_j_m2k':thickness*rho*cp}
wall=[layer(.009,.14,530,900),layer(.066,.04,12,840),layer(.012,.16,950,840)]
roof=[layer(.019,.14,530,900),layer(.1118,.04,12,840),layer(.010,.16,950,840)]
floor=[layer(1.003,.04,0,0),layer(.025,.14,650,1200)]
panel=[layer(.003048,1.,2470,750),layer(.012,.0625,1.292498,1006.103271),layer(.003048,1.,2470,750)]

def make(case):
 surfaces=[]
 for name,kind,area,layers,hi,ho,sky,ground in [
  ('north','wall',21.6,wall,2.2,11.9,.5,.5),('east','wall',16.2,wall,2.2,11.9,.5,.5),
  ('south','wall',9.6,wall,2.2,11.9,.5,.5),('west','wall',16.2,wall,2.2,11.9,.5,.5),
  ('roof','roof',48,roof,1.8,14.4,1,0),
  # Raised floor sees air, not soil: use a generic opaque surface boundary.
  ('raised_floor','wall',48,floor,2.2,.8,0,1),
  ('south_high_conductance','wall',12,panel,2.4,8,.5,.5)]:
  surfaces.append(dict(name=name,kind=kind,area_m2=area,layers=layers,
   resistance_m2k_w=sum(x['resistance_m2k_w'] for x in layers),thermal_mass_j_m2k=0,
   reflectance=.9,transmittance=0,emissivity=.9,hi_w_m2k=hi,ho_w_m2k=ho,
   sky_view=sky,ground_view=ground,ground_contact_w_m2k=0))
 gain=200 if case in (240,420) else 0
 infiltration=.5 if case in (410,420) else 0
 heat=20;cool=27 if case in (410,420) else 20
 hours=[]
 for i in range(8760):
  pressure=float(weather['Pressure (mbar)'].iloc[i])*100
  density=pressure/(287.05*(T[i]+273.15))
  humidity=0.621945*(float(weather['RHum (%)'].iloc[i])/100*610.94*np.exp(17.625*T[i]/(T[i]+243.04)))/max(1,pressure-(float(weather['RHum (%)'].iloc[i])/100*610.94*np.exp(17.625*T[i]/(T[i]+243.04))))
  hours.append(dict(air_c=float(T[i]),ground_c=float(T[i]),sky_ir_w_m2=float(SIGMA*(Ts[i]+273.15)**4),
   irradiance_w_m2=[*planes[i].tolist(),float(ghi[i]),0.,float(planes[i,2])],
   internal_w=gain,heat_setpoint_c=heat,cool_setpoint_c=cool,
   ventilation_w_k=float(density*(1006+1860*humidity)*129.6*infiltration/3600)))
 return dict(protocol='cetus-surface-7',dt_s=3600,surfaces=surfaces,hours=hours,initial_c=20,
  cooling_cop=1,heating_cop=1,internal_radiant_fraction=.6,solar_floor_fraction=0)

annual=[];monthly=[];outputs={}
ref=openpyxl.load_workbook(next(a.standard_files.rglob('Std140_TF_Results.xlsx')),data_only=True,read_only=True)
programs=['BSIMAC','CSE','DeST','EnergyPlus','ESP-r','TRNSYS']
with SurfaceEngine(timeout=300) as e:
 for case in [220,240,410,420]:
  model=make(case);cycle=copy.deepcopy(model);cycle['hours']=model['hours'][:24];state=None
  for k in range(120):
   if state is not None:cycle['initial_state']=state
   out=e.request([cycle])[0];new=out['final_state']
   change=float(np.max(np.abs(np.asarray(new)-state))) if state is not None else 1e6
   state=new
   if k>=3 and change<.001:break
  else:raise RuntimeError('Warmup did not converge')
  model['initial_state']=state
  result=e.request([model])[0];rows=result['hours'];outputs[case]=rows
  with gzip.GzipFile(str(a.output/f'case{case}_input.json.gz'),'wb',mtime=0) as f:f.write(json.dumps(model,separators=(',',':')).encode())
  h=np.maximum([r['hvac_w'] for r in rows],0)/1000;c=np.maximum([-r['hvac_w'] for r in rows],0)/1000
  pd.DataFrame([dict(hoy=i,hvac_w=r['hvac_w'],heating_kwh=h[i],cooling_kwh=c[i],internal_w=model['hours'][i]['internal_w'],balance_residual_w=r['balance_residual_w']) for i,r in enumerate(rows)]).to_csv(a.output/f'case{case}_hourly.csv',index=False)
  vals={'heating_mwh':float(h.sum()/1000),'cooling_mwh':float(c.sum()/1000),'peak_heating_kw':float(h.max()),'peak_cooling_kw':float(c.max())}
  summary=dict(case=case,hours=len(rows),warmup_cycles=k+1,max_balance_residual_w=max(abs(r['balance_residual_w']) for r in rows),**vals)
  rr=[]
  for prog in programs:
   sh=ref[prog]
   for row in sh.values:
    if len(row)>11 and str(row[1])==str(case):rr.append((prog,[row[2],row[3],row[4],row[8]]));break
  for j,key in enumerate(vals):
   values=[float(row[j]) for _,row in rr if isinstance(row[j],(int,float))]
   lo,hi=min(values),max(values);summary[key+'_reference_min']=lo;summary[key+'_reference_max']=hi
   summary[key+'_within_example_range']=bool(lo<=vals[key]<=hi)
  annual.append(summary);print(summary,flush=True)
 for base,with_gain in [(220,240),(410,420)]:
  q0=np.array([r['hvac_w'] for r in outputs[base]]);q1=np.array([r['hvac_w'] for r in outputs[with_gain]])
  monthly.append(dict(pair=f'{with_gain}-{base}',signed_hvac_change_kwh=float((q1-q0).sum()/1000),internal_gain_added_kwh=200*8760/1000))
pd.DataFrame(annual).to_csv(a.output/'annual_internal_gains.csv',index=False)
(a.output/'annual_summary.json').write_text(json.dumps({'cases':annual,'gain_sensitivities':monthly,
 'status':'Standard-based annual comparison; approximated solar transposition and lumped enclosure IR. Example-result intervals are not normative acceptance limits.'},indent=2))
# Recovered historical 744-hour inputs: optical gains are already prescribed.
reg=[];all_rows=[]
refday=pd.read_csv(a.recovered/'july14_reference_R5_reconstructed_v7.csv')
with SurfaceEngine() as e:
 for case in [600,660,670,680]:
  inp=json.loads(gzip.decompress((a.recovered/f'case{case}_input.json.gz').read_bytes()))
  original=json.loads(gzip.decompress((a.recovered/f'R5_case{case}.json.gz').read_bytes()))
  out=e.request([inp])[0];indices=[i for i,h in enumerate(original['hours']) if 4657<=h['hour_ending']<=4680]
  print('Reference columns:',refday.columns.tolist(),flush=True) if not reg else None
  truth=refday[refday['case']==case]['reference_kwh'].to_numpy()
  prediction=np.array([out['hours'][i]['hvac_w']/1000 for i in indices])
  reg.append(dict(case=case,hours=len(out['hours']),july14_rmse_kwh=float(np.sqrt(np.mean((prediction-truth)**2))),july14_pcc=float(np.corrcoef(prediction,truth)[0,1])))
  for i,row in enumerate(out['hours']):all_rows.append(dict(case=case,hour_ending=original['hours'][i]['hour_ending'],hvac_w=row['hvac_w'],balance_residual_w=row['balance_residual_w']))
pd.DataFrame(reg).to_csv(a.output/'recovered_july_metrics.csv',index=False)
pd.DataFrame(all_rows).to_csv(a.output/'recovered_july_hourly.csv',index=False)
print(json.dumps(reg,indent=2),flush=True)
