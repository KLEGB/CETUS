"""CETUS 1.9 example: explicit SHP/EPW, material optics and lighting controls."""
import sys,argparse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ENERGY import Neighborhood
from ENERGY.coupled import simulate_coupled
p=argparse.ArgumentParser();p.add_argument('shp',type=Path);p.add_argument('epw',type=Path)
p.add_argument('--output',type=Path,default=Path('outputs/cetus19'))
p.add_argument('--start-hoy',type=int,default=0);p.add_argument('--days',type=int,default=3)
p.add_argument('--visible-transmittance',type=float,required=True)
p.add_argument('--nir-transmittance',type=float,required=True)
p.add_argument('--threshold-lux',type=float,default=300)
p.add_argument('--control-off',action='store_true');p.add_argument('--sample',type=float,default=.1)
a=p.parse_args()
n=Neighborhood().readSHP(a.shp,fvc=.2,fwc=.05)
n.readEPW(a.epw,start_hoy=a.start_hoy,end_hoy=a.start_hoy+24*a.days,
          smooth_weather=False,climate_zone=4)
n.sampleInstance(seed=42)
options={'daylighting':{'enabled':not a.control_off,'visible_transmittance':a.visible_transmittance,
    'near_infrared_transmittance':a.nir_transmittance,'threshold_lux':a.threshold_lux}}
n.radSimulation(sample=a.sample,energy_options=options,output_path=a.output/'network.rad.npz')
r=simulate_coupled(n,options=options,output_dir=a.output/'coupled')
print(r['coupled']['lighting_summary'])
