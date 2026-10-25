"""Compare unchanged V8.4 to 1.9 control-off using identical optical materials."""
import sys,os,json,time,argparse
from pathlib import Path
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
p=argparse.ArgumentParser();p.add_argument('--baseline',required=True,type=Path);p.add_argument('--shp',required=True,type=Path)
p.add_argument('--epw',required=True,type=Path);p.add_argument('--network',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
a=p.parse_args();sys.path.insert(0,str(a.baseline.resolve()))
from ENERGY import Neighborhood
from ENERGY.coupled import simulate_coupled
n=Neighborhood().readSHP(a.shp,fvc=.2,fwc=.05)
n.readEPW(a.epw,start_hoy=5016,end_hoy=5088,smooth_weather=False,climate_zone=4);n.sampleInstance(seed=42)
for b in n.buildings:
 b.StoryHeight=float(b.height/(b.GFA/b.FloorArea));b._update_envelope_by_wwr()
 b.WindowTransmittance=.43*.7+.52*.2
for k in ['AbsoGround','AbsoRoad','AbsoLandscape','AbsoWater']:setattr(n.setting,k,.8)
for k in ['EmiGround','EmiRoad','EmiLandscape','EmiWater']:setattr(n.setting,k,.95)
a.output.mkdir(parents=True,exist_ok=True)
n.radSimulation(radFile=a.network,energy_options={},output_path=a.output/'network.rad.npz')
t=time.perf_counter()
r=simulate_coupled(n,options={},output_dir=a.output/'coupled',preheat=4,max_preheat=120)
(a.output/'timing.json').write_text(json.dumps({'coupled_seconds':time.perf_counter()-t}))
