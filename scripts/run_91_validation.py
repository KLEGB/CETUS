"""Reproduce five matched-material runs, independent plate, tables and figures."""
import argparse,sys,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--shp',type=Path,required=True);p.add_argument('--epw',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--cache',type=Path)
a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
script=Path(__file__).resolve().parent;module=script.parent
pairs=[('matched_one','matched_one_bounce'),('selective_one','selective_one_bounce'),('matched_full','matched_radiosity'),('selective_full','selective_radiosity'),('broadband_high','broadband_high_radiosity')]
cache=a.cache
for label,config in pairs:
 cmd=[sys.executable,str(script/'validate_91_case.py'),'--shp',str(a.shp.resolve()),'--epw',str(a.epw.resolve()),
  '--output',str(a.output/('91_'+label)),'--mode','on','--longwave-config',str(module/'configs'/f'{config}.json')]
 if cache:cmd+=['--cache',str(cache)]
 subprocess.run(cmd,check=True)
 if cache is None:cache=a.output/('91_'+label)/'network.rad.npz'
subprocess.run([sys.executable,str(script/'validate_91_plate.py'),str(a.output/'91_plate')],check=True)
subprocess.run([sys.executable,str(script/'compare_91_longwave.py'),'--root',str(a.output)],check=True)
