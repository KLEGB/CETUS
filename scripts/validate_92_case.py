"""Real SHP/EPW interface check: Neighborhood size, complete archive and meters."""
import argparse,sys,json,time,os
from pathlib import Path
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ENERGY import Neighborhood
from ENERGY.coupled import simulate_coupled
from result_store import read_results
p=argparse.ArgumentParser();p.add_argument('--shp',required=True,type=Path);p.add_argument('--epw',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
n=Neighborhood().readSHP(a.shp.resolve(),fvc=.2,fwc=.05)
n.readEPW(a.epw.resolve(),start_hoy=5016,end_hoy=5088,smooth_weather=False,climate_zone=4);n.sampleInstance(seed=42)
for b in n.buildings:b.StoryHeight=float(b.height/(b.GFA/b.FloorArea));b._update_envelope_by_wwr()
for k in ['AbsoGround','AbsoRoad','AbsoLandscape','AbsoWater']:setattr(n.setting,k,.8)
for k in ['EmiGround','EmiRoad','EmiLandscape','EmiWater']:setattr(n.setting,k,.95)
n.energy_options={'daylighting':dict(enabled=True,visible_transmittance=.7,near_infrared_transmittance=.2,threshold_lux=300,controlled_fraction=1.),'longwave':json.loads((Path(__file__).resolve().parents[1]/'configs/selective_one_bounce.json').read_text())}
n.radSimulation(sample=.1,energy_options=n.energy_options,output_path=a.output/'network.rad.npz')
n.saveJSON(a.output/'neighborhood_before.json');started=time.perf_counter()
r=simulate_coupled(n,output_dir=a.output/'coupled');elapsed=time.perf_counter()-started
n.saveJSON(a.output/'neighborhood_after.json');data=read_results(r['building_result_file'])
assert len(data['buildings'])==len(n.buildings)
assert all(len(x['hours'])==72 for x in data['buildings'])
assert all(not b.energyFeedback and b.ACLoad.size==0 for b in n.buildings)
report=dict(buildings=len(n.buildings),hours=72,neighborhood_before_bytes=(a.output/'neighborhood_before.json').stat().st_size,
 neighborhood_after_bytes=(a.output/'neighborhood_after.json').stat().st_size,building_results_bytes=Path(r['building_result_file']).stat().st_size,
 seconds=elapsed,warmup_cycles=len(r['coupled']['warmup']),lighting_summary=r['coupled']['lighting_summary'])
(a.output/'interface_report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
