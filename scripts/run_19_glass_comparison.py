"""Run four equal-solar glass/control cases, then summarize.
Use --shp, --epw, --output; uses the same seed and selected 72-hour period.
"""
import argparse,subprocess,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--shp',required=True,type=Path);p.add_argument('--epw',required=True,type=Path)
p.add_argument('--output',required=True,type=Path);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
script=Path(__file__).resolve().parent
for folder,mode,tv,tn in [('shp_on','on',.7,.2),('shp_off','off',.7,.2),
                         ('glass_normal_on','on',.405/.95,.405/.95),('glass_normal_off','off',.405/.95,.405/.95)]:
 cmd=[sys.executable,str(script/'validate_19_case.py'),'--shp',str(a.shp.resolve()),'--epw',str(a.epw.resolve()),
      '--output',str(a.output/folder),'--mode',mode,'--visible-transmittance',str(tv),'--nir-transmittance',str(tn)]
 if folder!='shp_on':cmd+=['--cache',str(a.output/'shp_on/network.rad.npz')]
 subprocess.run(cmd,check=True)
subprocess.run([sys.executable,str(script/'compare_19_glass.py'),'--root',str(a.output)],check=True)
