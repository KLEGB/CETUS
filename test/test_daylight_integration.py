"""Electrical/thermal accounting, restart, and scalar-only public API tests."""
import copy
import unittest
import numpy as np
from test.test_coupled import small
from ENERGY import simulate_surface
from ENERGY.surface import build_surface_input
from ENERGY.coupled import simulate_coupled


class DaylightIntegrationTests(unittest.TestCase):
    def options(self, enabled=True):
        return {'daylighting':{'enabled':enabled,'visible_transmittance':.7,
            'near_infrared_transmittance':.2,'threshold_lux':300}}

    def test_control_off_preserves_glass_and_substitutes_heat_once(self):
        n=small();b=n.buildings[0];c=n.climate[0]
        irr={'facade':np.full(24,500.),'roof':np.full(24,500.)}
        p,a=build_surface_input(b,c,irradiance=irr,options=self.options(False))
        q,d=build_surface_input(b,c,irradiance=irr,options=self.options())
        self.assertEqual(p['surfaces'],q['surfaces'])
        self.assertAlmostEqual(next(s for s in p['surfaces'] if s['name']=='window')['transmittance'],.405)
        for i,(h,j) in enumerate(zip(p['hours'],q['hours'])):
            self.assertAlmostEqual(h['internal_w']-j['internal_w'],
                a['lighting_electricity_w'][i]-d['lighting_electricity_w'][i])
        self.assertEqual(q,build_surface_input(b,c,irradiance=irr,options=self.options())[0])

    def test_scalar_only_matches_geometry_derived_envelope(self):
        n=small(2);b=n.buildings[0]
        a=simulate_surface(b,n.climate,preheat=0,options=self.options())
        scalar=copy.deepcopy(b);scalar._perimeter=0;scalar.height=0
        scalar.RoofArea=b.RoofArea
        z=simulate_surface(scalar,n.climate,preheat=0,options=self.options())
        np.testing.assert_allclose(a['final_state'],z['final_state'],atol=1e-9)
        self.assertEqual(a['lighting_summary'],z['lighting_summary'])

    def test_restart_and_spinup_meters(self):
        options=self.options()
        full=simulate_coupled(small(2),preheat=0,show_progress=False,options=options)
        first=simulate_coupled(small(),preheat=0,show_progress=False,options=options)
        n=small(2);n.climate=n.climate[1:]
        second=simulate_coupled(n,initial_state=first['coupled']['final_state'],
            preheat=0,show_progress=False,options=options)
        np.testing.assert_allclose(second['Tair'][:,0],full['Tair'][:,1],atol=1e-8)
        spin=simulate_coupled(small(2),preheat=0,spinup_days=1,show_progress=False,options=options)
        self.assertAlmostEqual(spin['coupled']['lighting_summary']['lighting_electricity_kwh'],
            second['coupled']['lighting_summary']['lighting_electricity_kwh'])
        self.assertEqual(len(spin['hourly']),24)

    def test_control_change_rejects_stale_restart(self):
        r=simulate_coupled(small(),preheat=0,show_progress=False,options=self.options())
        n=small(2);n.climate=n.climate[1:]
        with self.assertRaises(ValueError):
            simulate_coupled(n,preheat=0,initial_state=r['coupled']['final_state'],
                options=self.options(False),show_progress=False)

    def test_reflection_closure_and_option_typo(self):
        n=small();b=n.buildings[0];c=n.climate[0]
        with self.assertRaises(ValueError):
            build_surface_input(b,c,options={'daylighting':{'threshhold_lux':300}})
        with self.assertRaises(ValueError):
            build_surface_input(b,c,irradiance={'facade':np.ones(24)*20,
                'roof':np.zeros(24),'facade_reflected':np.ones(24)*10},options=self.options())

    def test_no_cop_division_of_lighting(self):
        n=small();b=n.buildings[0]
        b.CoolingEER=4;b.HeatingEER=3
        r=simulate_surface(b,n.climate,preheat=0,options=self.options(False))
        for row in r['hours']:
            self.assertAlmostEqual(row['hvac_plus_lighting_electricity_w']-row['electricity_w'],
                row['lighting_electricity_w'])

    def test_analytical_electrical_thermal_lamp_balance(self):
        from ENERGY import BuildingInstance
        from UHI.modelInfo import ClimateInfo
        b=BuildingInstance(StoryHeight=3,FloorArea=100,GFA=100,PerimeterZoneArea=60,
            ExteriorWallArea=100,RoofArea=100,GroundFloorArea=100,
            WallUValue=.6,WindowUValue=2.4,CoolingEER=4,HeatingEER=3,
            LightingHeatGain=2.,HeatingSetpointTemp=20,CoolingSetpointTemp=20)
        c=ClimateInfo(HOY=np.arange(24),Ta=np.full(24,20.),Tg=np.full(24,20.),
                      SkyTemperature=np.full(24,20.),RadTotal=np.zeros(24),RadDiffuse=np.zeros(24))
        options=self.options();options['internal_radiant_fraction']=0
        result=simulate_surface(b,[c],preheat=0,options=options)
        # Isothermal surroundings, fixed zone at 20 C: all 200 W convective
        # lamp heat must be removed by ideal cooling. COP=4 adds 50 W HVAC.
        np.testing.assert_allclose([r['hvac_w'] for r in result['hours']],-200.,atol=1e-6)
        self.assertAlmostEqual(result['lighting_summary']['lighting_electricity_kwh'],4.8)
        self.assertAlmostEqual(result['lighting_summary']['hvac_electricity_kwh'],1.2)
        self.assertAlmostEqual(result['lighting_summary']['hvac_plus_lighting_electricity_kwh'],6.)


if __name__=='__main__':unittest.main()
