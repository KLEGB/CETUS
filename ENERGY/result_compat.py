"""Externalize historical steady/legacy entry points after city feedback commit."""
from pathlib import Path
from .result_store import ResultWriter,plain,read_results

def previous_results(n):
 if not n.building_result_file:return []
 return read_results(n.building_result_file)['context'].get('compat_results',[])

def persist(n,results,folder,engine):
 n.syncSiteGFA();previous=previous_results(n) if len(results)<len(n.buildings) else []
 all_results={int(x['building_index']):plain(x) for x in previous}
 all_results.update({int(x['building_index']):plain(x) for x in results})
 if set(all_results)!=set(range(len(n.buildings))):raise ValueError('Incomplete building results')
 values=[all_results[i] for i in range(len(n.buildings))]
 hoy=[int(h) for c in n.climate for h in c.HOY]
 with ResultWriter(Path(folder)/'building_results.bin',[b.input_dict() for b in n.buildings],context={'engine':engine,'compat_results':values}) as w:
  for t,h in enumerate(hoy):
   rows=[]
   for item in values:
    rows.append({k:v[t] for k,v in item.items() if isinstance(v,list) and len(v)==len(hoy) and k!='energyHOY'})
   w.record(h,rows=rows)
  n.building_result_file=w.finish({'engine':engine})
 for b in n.buildings:b.clear_results()
 n._energy_results=None
