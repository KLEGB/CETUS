"""Scalar-only, two-floor example. Run from any directory; no geometry required."""
import sys,json
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ENERGY import BuildingInstance,simulate_surface
from UHI.modelInfo import ClimateInfo
b=BuildingInstance(StoryHeight=3.,FloorArea=100.,GFA=200.,PerimeterZoneArea=60.,
    ExteriorWallArea=60.,ExteriorWindowArea=20.,RoofArea=100.,GroundFloorArea=100.,
    WallUValue=.6,WindowUValue=2.4,CoolingEER=3.,HeatingEER=3.,
    HeatingSetpointTemp=20.,CoolingSetpointTemp=26.,LightingHeatGain=8.,
    EquipmentHeatGain=3.,OccupantDensity=.1,OccupantHeatGain=75.,
    InfiltrationACH=.5,NightVentilationACH=.5)
climates=[ClimateInfo(HOY=np.arange(d*24,(d+1)*24),Ta=np.full(24,28.),
    Tg=np.full(24,24.),SkyTemperature=np.full(24,18.),
    RadTotal=np.maximum(0,np.sin((np.arange(24)-6)*np.pi/12))*700,
    RadDiffuse=np.maximum(0,np.sin((np.arange(24)-6)*np.pi/12))*150) for d in range(2)]
options={'daylighting':{'enabled':True,'visible_transmittance':.7,
    'near_infrared_transmittance':.2,'threshold_lux':300,
    'rooms_by_floor':{0:[{'name':'window_room','area_m2':50,'has_window':True},
        {'name':'inner_room','area_m2':50,'has_window':False}]}}}
options['longwave']=json.loads((Path(__file__).resolve().parents[1]/'configs/selective_one_bounce.json').read_text())
result=simulate_surface(b,climates,options=options,output_dir=Path(__file__).resolve().parents[1]/'outputs/standalone_91')
print(json.dumps(result['lighting_summary'],indent=2))
