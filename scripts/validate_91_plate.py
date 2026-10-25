"""Independent isolated-plate mechanism test; no geometric transport truncation."""
import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from scipy.optimize import brentq
from UHI.spectral import exact_band_exitance,SIGMA
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
bands=[(0,8),(8,13),(13,None)]
B=lambda t:np.array([float(exact_band_exitance(t,*b)) for b in bands])
ref=B(300);eps_selective=np.array([.1,.95,.1]);eps_mean=float(eps_selective@ref/ref.sum())
rows=[]
for window_tau in [0.,.25,.5,.75,1.]:
 tau=np.array([.02,window_tau,.02]);incoming=(1-tau)*B(300)
 for name,eps in [('matched_grey',np.full(3,eps_mean)),('selective',eps_selective),('high_grey',np.full(3,.9))]:
  q300=float(eps@(B(300)-incoming))
  for solar_absorbed in [0.,200.]:
   def f(t):return solar_absorbed+8*(300-t)-float(eps@(B(t)-incoming))
   t=brentq(f,220,380)
   rows.append(dict(material=name,window_atmospheric_transmittance=window_tau,ambient_k=300.,
       absorbed_shortwave_w_m2=solar_absorbed,convection_w_m2k=8.,equilibrium_surface_c=t-273.15,
       net_cooling_at_ambient_w_m2=q300,balance_residual_w_m2=f(t)))
(out/'plate.json').write_text(json.dumps(rows,indent=2))
import pandas as pd
pd.DataFrame(rows).to_csv(out/'plate.csv',index=False)
print(json.dumps(rows,indent=2))
