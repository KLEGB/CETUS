"""Reproducible V8.2 case: chronological weather spinup, native flux diagnostics."""
import argparse,json,sys,time
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from ENERGY import Neighborhood
from ENERGY.surface import build_surface_input,SIGMA
from UHI.aT import referenceVelocity
from WEA.epw import load_epw_rows
p=argparse.ArgumentParser();p.add_argument('shp',type=Path);p.add_argument('epw',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--sky',type=Path);p.add_argument('--spinup-days',type=int,default=7);p.add_argument('--seed',type=int,default=42);p.add_argument('--sample',type=float,default=.3);p.add_argument('--checkpoint',type=Path);a=p.parse_args()
a.output.mkdir(parents=True,exist_ok=True);_,rows,_=load_epw_rows(a.epw);t=np.array([float(r[6]) for r in rows]);target=int(t.reshape(-1,24).mean(1).argmax()*24);start=target-a.spinup_days*24
if start<0:raise ValueError('Insufficient preceding EPW days; supply an earlier-year restart instead')
n=Neighborhood().readSHP(a.shp,fvc=.2,fwc=.05);n.readEPW(a.epw,start_hoy=start,end_hoy=target+24,smooth_weather=False,climate_zone=4);n.sampleInstance(seed=a.seed)
for b in n.buildings:b.StoryHeight=float(b.height/(b.GFA/b.FloorArea));b._update_envelope_by_wwr()
for k in ['AbsoGround','AbsoRoad','AbsoLandscape','AbsoWater']:setattr(n.setting,k,.8)
for k in ['EmiGround','EmiRoad','EmiLandscape','EmiWater']:setattr(n.setting,k,.95)
started=time.perf_counter();n.radSimulation(sample=a.sample,sky_matrix=a.sky,output_path=a.output/'network.rad.npz')
(a.output/'site.json').write_text(json.dumps(n.site.to_dict()));(a.output/'settings.json').write_text(json.dumps(n.setting.to_dict()))
result=n.uhiSimulation(energy_model='v7',preheat=4,energy_kwargs={'output_dir':a.output,'spinup_days':a.spinup_days,'max_preheat':30,'warmup_acceleration':True,'warmup_checkpoint':a.checkpoint,'show_progress':True})
# Export accepted forcing rather than optical-model proxy irradiation.
models=[];c=n.climate[0];sol=n.radiation_network.solar(c.HOY)
for b,building in enumerate(n.buildings):
 m,_=build_surface_input(building,c,irradiance={'facade':sol[2*b],'roof':sol[2*b+1]})
 for h,(x,o,row) in enumerate(zip(m['hours'],building.energyFeedback['surface_v7'],result['hourly'])):
  x['air_c']=row['Tair'];x['ground_c']=row['Tsurface'];ref=referenceVelocity(float(c.Velocity[h]),n.site.kS,n.setting.a,n.setting.k)
  x['ho_w_m2k']=[0. if s['kind']=='floor' else 5.8+4.1*ref for s in m['surfaces']]
  x['network_ir_w_m2']=[0. if s['kind']=='floor' else SIGMA*(r['exterior_c']+273.15)**4-r['external_net_lw_w']/(s['area_m2']*s['emissivity']) for s,r in zip(m['surfaces'],o['surfaces'])]
 m['initial_state']=result['coupled']['report_initial_state']['buildings'][b]
 models.append(m)
(a.output/'models.json').write_text(json.dumps(models,separators=(',',':')))
summary={'version':'8.2','target_hoy':target,'spinup_days':a.spinup_days,'warmup':result['coupled']['warmup'],'buildings':len(n.buildings),'site_area_m2':n.site.TotalArea,'seconds':time.perf_counter()-started,'maximum_total_residual_w_m2':max(abs(r['total_residual_w_m2']) for r in result['hourly']),'cooling_Wh_m2_site':sum(sum(b.coolingLoad) for b in n.buildings)/n.site.TotalArea,'electricity_Wh_m2_site':sum(sum(h['electricity_w'] for h in b.energyFeedback['surface_v7']) for b in n.buildings)/n.site.TotalArea}
(a.output/'run.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2),flush=True)
