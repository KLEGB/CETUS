"""SHP/EPW run: explicit 1.9 controls; JSON summary and complete diagnostics."""
import os,sys,time,json,argparse
from pathlib import Path
os.environ.setdefault('OPENBLAS_NUM_THREADS','1');os.environ.setdefault('OMP_NUM_THREADS','1')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from ENERGY import Neighborhood
from ENERGY.coupled import simulate_coupled
from WEA.epw import load_epw_rows
p=argparse.ArgumentParser();p.add_argument('--shp',required=True,type=Path);p.add_argument('--epw',required=True,type=Path)
p.add_argument('--output',required=True,type=Path);p.add_argument('--days',type=int,default=3)
p.add_argument('--mode',choices=['legacy','off','on'],default='on');p.add_argument('--start-hoy',type=int)
p.add_argument('--sample',type=float,default=.1);p.add_argument('--cache',type=Path)
p.add_argument('--visible-transmittance',type=float,default=.7)
p.add_argument('--nir-transmittance',type=float,default=.2)
p.add_argument('--other-transmittance',type=float,default=0.)
p.add_argument('--longwave-config',type=Path)
a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
_,weather,_=load_epw_rows(a.epw)
start=int(np.argmax(np.asarray([float(r[6]) for r in weather]).reshape(-1,24).mean(1)))*24 if a.start_hoy is None else a.start_hoy
n=Neighborhood().readSHP(a.shp,fvc=.2,fwc=.05)
n.readEPW(a.epw,start_hoy=start,end_hoy=start+a.days*24,smooth_weather=False,climate_zone=4)
n.sampleInstance(seed=42)
# Keep the stock's GFA/height relation, as the V8.4 efficiency test did.
for b in n.buildings:
 b.StoryHeight=float(b.height/(b.GFA/b.FloorArea));b._update_envelope_by_wwr()
for k in ['AbsoGround','AbsoRoad','AbsoLandscape','AbsoWater']:setattr(n.setting,k,.8)
for k in ['EmiGround','EmiRoad','EmiLandscape','EmiWater']:setattr(n.setting,k,.95)
options={} if a.mode=='legacy' else {'daylighting':dict(enabled=a.mode=='on',visible_transmittance=a.visible_transmittance,
    near_infrared_transmittance=a.nir_transmittance,other_transmittance=a.other_transmittance,
    threshold_lux=300,controlled_fraction=1.)}
if a.longwave_config:options['longwave']=json.loads(a.longwave_config.read_text())
t=time.perf_counter()
if a.cache:n.radSimulation(radFile=a.cache,energy_options=options,output_path=a.output/'network.rad.npz')
else:n.radSimulation(sample=a.sample,energy_options=options,output_path=a.output/'network.rad.npz')
rad_time=time.perf_counter()-t;t=time.perf_counter()
r=simulate_coupled(n,options=options,output_dir=a.output/'coupled',preheat=4,max_preheat=120)
from export_92_diagnostics import export_compat
external=export_compat(r['building_result_file'],a.output/'coupled')
rows=[row for b in external['buildings'] for row in b['hours']]
summary=dict(mode=a.mode,buildings=len(n.buildings),hours=len(r['hourly']),start_hoy=start,
    floor_area_m2=sum(b.GFA for b in n.buildings),rad_seconds=rad_time,coupled_seconds=time.perf_counter()-t,
    max_site_balance_w_m2=max(abs(x['total_residual_w_m2']) for x in r['hourly']),
    max_building_balance_w=max(abs(x['balance_residual_w']) for x in rows),
    max_shortwave_partition_residual_w=max(abs(s['incident_w']-s['absorbed_w']-s['reflected_w']-s['transmitted_w']) for x in rows for s in x['surfaces']),
    max_window_storage_w=max(abs(s['storage_w']) for x in rows for s in x['surfaces'] if s['name'] in ('window','skylight')),
    mean_uhi_k=float(np.mean(r['Tair']-r['Tambient'])),lighting=r['coupled']['lighting_summary'],
    warmup_cycles=len(r['coupled']['warmup']),seed=42,sample=a.sample,
    sky='Isotropic diffuse plus nearest-patch sun, hourly GHI normalized; not Perez',
    options=options)
if a.longwave_config:
 summary['spectral']={key:max(abs(x[key]) for x in r['hourly']) for key in ('lw_spectral_transport_residual_w','lw_spectral_max_band_residual_w','lw_spectral_engine_difference_w','lw_second_reflection_unresolved_w')}
 summary['roof_mean_c']=float(np.mean([s['exterior_c'] for row in rows for s in row['surfaces'] if s['name']=='roof']))
 summary['longwave_building_net_out_kwh']=sum(x['building_net_lw_w'] for x in r['hourly'])/1000
 summary['second_reflection_unresolved_kwh']=sum(x['lw_second_reflection_unresolved_w'] for x in r['hourly'])/1000
 summary['space_escape_kwh']=sum(x['lw_space_escape_w'] for x in r['hourly'])/1000
 import pandas as pd
 pd.DataFrame([dict(hoy=row['hoy'],**band) for row in r['hourly'] for band in row['lw_spectral_bands']]).to_csv(a.output/'coupled/longwave_bands_hourly.csv',index=False)
(a.output/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2),flush=True)
