"""Run a Neighborhood JSON and write all building results as CER binary."""
import argparse
from pathlib import Path
from ENERGY import Neighborhood
from ENERGY.coupled import simulate_coupled
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--input',required=True);p.add_argument('--output',required=True);a=p.parse_args()
 source=Path(a.input).resolve();target=Path(a.output).resolve();n=Neighborhood.loadJSON(source)
 for name in ('_shp','_epw','_weather_csv','_mtx','_rad'):
  value=getattr(n,name)
  if value and not Path(value).is_absolute():setattr(n,name,str(source.parent/value))
 r=simulate_coupled(n,output_dir=target.parent/(target.stem+'_city'))
 produced=Path(r['building_result_file'])
 if produced!=target:produced.replace(target)
 n.building_result_file=str(target);n.saveJSON(target.with_name(target.stem+'_neighborhood.json'))
 print(target)
