"""Editor persistence, selection, templates and real 9.2 N/F integration."""
import copy
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import box

from ENERGY import Neighborhood
from ENERGY.model import BuildingInstance
from UHI.modelInfo import ClimateInfo, SiteInfo
from view.app import create_app
from view.model import Editor, templates, date_window, daily, apply_values, FIELDS, fresh_state
from view.simulation import run_comparison


def shp(tmp_path,column='height'):
    path=tmp_path/'source.shp'
    gpd.GeoDataFrame({column:[8.,12.]},geometry=[box(0,0,10,10),box(30,30,40,40)],crs='EPSG:32650').to_file(path)
    return path


def test_height_prompt_and_transaction(tmp_path):
    e=Editor(tmp_path/'work');path=shp(tmp_path,'custom_h')
    assert e.import_shp(path)['needs_height']
    assert not e.n.buildings
    with pytest.raises(ValueError):e.import_shp(path,'missing')
    assert not e.n.buildings
    response=e.import_shp(path,'custom_h')
    assert not response['needs_height']
    assert len(e.n.buildings)==2
    assert all(b.archetype=='default' for b in e.n.buildings)
    assert [b.height for b in e.n.buildings]==[8,12]


def test_defaults_daily_and_template_apply(tmp_path):
    e=Editor(tmp_path/'work');e.import_shp(shp(tmp_path))
    t=templates()['office'];ids=[e.n.buildings[0].building_id]
    other=e.n.buildings[1].input_dict()
    e.apply_buildings(ids,t['values'],{k:0 for k in FIELDS},'office')
    b=e.n.buildings[0]
    assert b.archetype=='office'
    assert b.WallUValue==t['values']['WallUValue']
    assert e.n.buildings[1].input_dict()==other
    e.apply_buildings(ids,{'EquipmentHeatGain':list(range(24))},{'EquipmentHeatGain':0})
    values=b=e.n.buildings[0].schedule.entries[e.n.buildings[0].EquipmentHeatGain].hourly_values
    np.testing.assert_array_equal(values[:24],np.arange(24))
    np.testing.assert_array_equal(values[24:48],values[:24])


def test_portable_save_roundtrip_and_legacy_load(tmp_path):
    e=Editor(tmp_path/'work');e.import_shp(shp(tmp_path))
    e.state['selected']=[e.n.buildings[0].building_id]
    target=tmp_path/'saved'/'project.json';e.save(target)
    raw=json.loads(target.read_text(encoding='utf-8'))
    assert not Path(raw['_shp']).is_absolute()
    assert (target.parent/Path(raw['_shp']).with_suffix('.dbf')).is_file()
    moved=tmp_path/'moved'
    import shutil
    shutil.copytree(target.parent,moved)
    n=Neighborhood.loadJSON(moved/'project.json')
    assert Path(n._shp).parent==moved
    other=Editor(tmp_path/'other');other.load(moved/'project.json')
    assert other.state['selected']==e.state['selected']
    assert len(other.geometries)==2
    raw.pop('view_state');(moved/'legacy.json').write_text(json.dumps(raw),encoding='utf-8')
    other.load(moved/'legacy.json')
    assert other.state['selected']==[]
    assert other.state['building_parameters']=={}
    assert other.state['lcz']=='LCZx'


def test_api_options_random_selection_and_urban_history(tmp_path):
    app=create_app(tmp_path/'work');client=app.test_client();e=app.extensions['editor']
    e.import_shp(shp(tmp_path))
    assert client.get('/').status_code==200
    assert client.get('/api/state').status_code==200
    assert client.post('/api/options',json={'iteration':'11'}).status_code==400
    assert client.post('/api/options',json={'iteration':'x','sample':.3}).status_code==200
    p={'random':True,'ratio':.5,'seed':5}
    a=client.post('/api/select',json=p).json['selected'];b=client.post('/api/select',json=p).json['selected']
    assert a==b and len(a)==1
    original=e.urban();bad=copy.deepcopy(original);bad.update(FVC=1,FWC=1)
    assert client.post('/api/urban',json={'data':bad}).status_code==400
    assert e.urban()==original
    good=copy.deepcopy(original);good['setting']['GroundTemperature']=22.
    assert client.post('/api/urban',json={'data':good}).status_code==200
    e.remember_urban();e.apply_urban({},'LCZ1');e.restore_urban()
    assert e.n.setting.GroundTemperature==22.
    assert client.post('/api/select',json={'ids':[]},headers={'Origin':'https://example.com'}).status_code==403


def small_neighborhood(tmp_path):
    e=Editor(tmp_path/'work')
    e.import_shp(shp(tmp_path))
    n=e.n
    c=ClimateInfo();c.HOY=np.arange(24,dtype=np.int32)
    c.Tb=np.full(24,30.);c.Ta=c.Tb.copy();c.Tg=np.full(24,28.)
    c.SkyTemperature=np.full(24,22.);c.Velocity=np.full(24,1.5)
    c.Pressure=np.full(24,101325.);c.Rh=np.full(24,60.)
    c.RadTotal=np.maximum(0,np.sin((np.arange(24)-6)*np.pi/12))*650
    c.RadDiffuse=c.RadTotal*.3
    n.climate=[c];n._start_hoy=0;n._end_hoy=24;n._expanded_start_hoy=0;n._expanded_end_hoy=24
    n.view_state.update(weather_loaded=True,iteration='2',sample=.05,load_tolerance=.1)
    n.setting.GroundTemperature=25.
    return n


def test_real_surface_engine_comparison(tmp_path):
    n=small_neighborhood(tmp_path)
    before=[b.input_dict() for b in n.buildings]
    result=run_comparison(n,tmp_path/'run',report=lambda _:None)
    assert len(result['N'])==len(result['F'])==24
    assert result['iterations']==2
    assert len(result['history'])==2
    assert result['total_gfa']==sum(b.GFA for b in n.buildings)
    assert np.max(np.abs(n.climate[0].ACWasteHeat))>0
    np.testing.assert_allclose(n.climate[0].coolingLoad,n.climate[0].ACWasteHeat/n.site.TotalArea)
    assert [b.input_dict() for b in n.buildings]==before
    assert all(r['max_residual']<=.02 for r in result['F'])
    assert any(abs(a['Ta']-b['Ta'])>1e-6 for a,b in zip(result['N'],result['F']))
    for mode in ('N','F'):
        archive=json.loads(Path(result['paths'][mode]['json']).read_text(encoding='utf-8'))
        assert len(archive['buildings'])==2
        assert len(archive['buildings'][0]['hours'])==24
        for h,row in enumerate(result[mode]):
            expected=sum(max(0,-b['hours'][h]['hvac_w']) for b in archive['buildings'])/result['total_gfa']
            assert row['Load']==pytest.approx(expected)


def test_date_window():
    assert date_window('01-01','01-01')==(0,24)
    assert date_window('12-31','12-31')==(8736,8760)
    with pytest.raises(ValueError):date_window('02-29','03-01')


def test_worker_lifecycle_and_cancel(tmp_path):
    import time
    app=create_app(tmp_path/'app');client=app.test_client();editor=app.extensions['editor']
    editor.n=small_neighborhood(tmp_path)
    response=client.post('/api/run',json={'iteration':'1'})
    assert response.status_code==200
    job=response.json['id']
    assert client.post('/api/select',json={'ids':[]}).status_code==400
    deadline=time.monotonic()+45
    while time.monotonic()<deadline:
        status=client.get(f'/api/jobs/{job}').json
        if status['status']!='running':break
        time.sleep(.2)
    assert status['status']=='complete',status
    assert status['result']['iterations']==1
    assert client.get(f'/api/jobs/{job}/download/F_hourly.csv').status_code==200
    assert client.get(f'/api/jobs/{job}/download/input.json').status_code==400
    second=client.post('/api/run',json={'iteration':'x'}).json['id']
    assert client.post(f'/api/jobs/{second}/stop',json={}).json['status']=='cancelled'
    assert client.get(f'/api/jobs/{second}').json['status']=='cancelled'
