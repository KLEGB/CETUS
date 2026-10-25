import copy
import random
import tempfile
import zipfile
from pathlib import Path
import unittest
import numpy as np
from shapely.geometry import box
from ENERGY import Neighborhood, BuildingInstance
from ENERGY.coupled import simulate_coupled
from ENERGY.surface import build_surface_input
from UHI.modelInfo import ClimateInfo, SiteInfo, settingInfo


def small(days=1):
    b=BuildingInstance().fromAOI(box(0,0,10,10),8)
    b.fromArchetype(3,'office',rng=random.Random(42))
    n=Neighborhood(buildings=[b],site=SiteInfo(TotalArea=10000,AreaBuilding=100,AreaFacade=320,
        AreaRoad=3000,AreaGround=2900,AreaLandscape=3000,AreaWater=1000),setting=settingInfo())
    n.climate=[ClimateInfo(HOY=np.arange(d*24,(d+1)*24),Ta=np.full(24,25.),Tg=np.full(24,25.),
        RadTotal=np.maximum(0,np.sin((np.arange(24)-6)*np.pi/12))*700,
        RadDiffuse=np.maximum(0,np.sin((np.arange(24)-6)*np.pi/12))*150,
        SkyTemperature=np.full(24,15.)) for d in range(days)]
    return n


class CoupledTest(unittest.TestCase):
    def test_conservation_and_split(self):
        n=small(2)
        full=simulate_coupled(n,preheat=0,show_progress=False)
        a=small();first=simulate_coupled(a,preheat=0,show_progress=False)
        b=small(2);b.climate=b.climate[1:]
        second=simulate_coupled(b,initial_state=first['coupled']['final_state'],preheat=0,show_progress=False)
        self.assertLess(np.max(np.abs(second['Tair'][:,0]-full['Tair'][:,1])),1e-7)
        self.assertEqual(second['coupled']['final_state'],full['coupled']['final_state'])
        self.assertLess(max(abs(r['total_residual_w_m2']) for r in full['hourly']),.02)
        self.assertTrue(all(r['building_shortwave_in_uhi_w_m2']==0 for r in full['hourly']))
        self.assertAlmostEqual(sum(full['coupled']['exposed_fractions'].values()),.99)
        from ENERGY.result_store import read_results
        for r in read_results(full['building_result_file'])['buildings'][0]['hours']:
            self.assertLess(abs(r['balance_residual_w']),1e-4)
            self.assertTrue(all(s['storage_w']==0 for s in r['surfaces'] if s['name'] in {'window','skylight'}))

    def test_warmup_warning_continues(self):
        import io,contextlib
        n=small();buffer=io.StringIO()
        with self.assertWarns(RuntimeWarning), contextlib.redirect_stdout(buffer):
            result=simulate_coupled(n,preheat=1,max_preheat=1,warmup_tolerance_k=1e-12,show_progress=False)
        self.assertEqual(buffer.getvalue(),'')
        self.assertEqual(len(result['hourly']),24)
        self.assertTrue(Path(result['building_result_file']).is_file())

    def test_bad_restart(self):
        n=small();result=simulate_coupled(n,preheat=0,show_progress=False)
        with self.assertRaises(ValueError):simulate_coupled(small(),initial_state=result['coupled']['final_state'],preheat=0)

    def test_skylight_zero_mass_and_geometry(self):
        n=small();b=n.buildings[0];b.SkylightArea=20
        p,_=build_surface_input(b,n.climate[0]);surfaces={s['name']:s for s in p['surfaces']}
        self.assertEqual(surfaces['skylight']['thermal_mass_j_m2k'],0)
        self.assertEqual(surfaces['roof']['area_m2']+surfaces['skylight']['area_m2'],b.RoofArea)
        with self.assertRaises(ValueError):build_surface_input(b,n.climate[0],options={'surfaces':{'window':{'thermal_mass_j_m2k':1}}})

    def test_cover_area_budget(self):
        from test.paths import VALIDATION
        archive=VALIDATION/'coupled_input/53695_3092_LCZ1_2B.zip'
        with tempfile.TemporaryDirectory() as folder:
            with zipfile.ZipFile(archive) as z:z.extractall(folder)
            n=Neighborhood().readSHP(next(Path(folder).rglob('*.shp')),fvc=.2,fwc=.05)
        areas=sum(getattr(n.site,'Area'+k) for k in ('Building','Road','Ground','Landscape','Water'))
        self.assertAlmostEqual(areas,n.site.TotalArea)
        self.assertAlmostEqual(n.site.AreaLandscape/n.site.TotalArea,.2)
        self.assertAlmostEqual(n.site.AreaWater/n.site.TotalArea,.05)

    def test_public_entrypoint(self):
        result=small().uhiSimulation(energy_model='v7',preheat=0,energy_kwargs={'show_progress':False})
        self.assertEqual(result['Tair'].shape,(24,1))

if __name__=='__main__':unittest.main()
