"""Explicit optional analysis export for historical validation scripts only.
The Energy runtime itself does not generate full building JSON.
"""
from pathlib import Path
import json,csv
from result_store import read_results

def export_compat(path,folder):
 data=read_results(path);folder=Path(folder)
 (folder/'building_hourly.json').write_text(json.dumps([b['hours'] for b in data['buildings']],separators=(',',':')))
 records=[]
 for day,daily in enumerate(data['context'].get('day_audits',[])):
  for b,audit in enumerate(daily):
   d=audit.get('daylighting')
   if not d:continue
   for h in range(24):
    for group,size in d['group_areas_m2'].items():
     records.append(dict(building_index=b,hoy=data['buildings'][b]['hours'][day*24+h]['hoy'],group=group,area_m2=size,daylight_lux=d['illuminance_lux_by_group'][group][h],calls_for_light=d['lights_on_by_group'][group][h]))
 if records:
  with (folder/'daylighting_hourly.csv').open('w',newline='') as f:
   w=csv.DictWriter(f,fieldnames=records[0]);w.writeheader();w.writerows(records)
 return data
