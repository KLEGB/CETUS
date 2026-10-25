"""Replay generated annual fixtures without downloading the full standard ZIP.

Usage: python scripts/replay_19_annual.py /path/to/results/ashrae
This checks reproducibility of the delivered results, not standard conformance.
"""
import sys,gzip,json
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ENERGY.surface import SurfaceEngine
folder=Path(sys.argv[1]);rows=[]
with SurfaceEngine(timeout=300) as engine:
 for case in [220,240,410,420]:
  p=json.loads(gzip.decompress((folder/f'case{case}_input.json.gz').read_bytes()))
  r=engine.request([p])[0];q=np.array([h['hvac_w'] for h in r['hours']])
  expected=pd.read_csv(folder/f'case{case}_hourly.csv')['hvac_w'].to_numpy()
  diff=float(np.max(np.abs(q-expected)));assert diff<1e-5
  rows.append(dict(case=case,hours=len(q),max_load_difference_w=diff))
print(json.dumps(rows,indent=2))
