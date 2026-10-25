"""Editor data, templates, transactional imports and portable project saving."""
from __future__ import annotations

import copy
import csv
import json
import math
import random
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import numpy as np

from ENERGY import Neighborhood
from ENERGY.model import BuildingInstance, Schedule, TEMPLATE_TYPE_MAP, ARCHETYPE_MEAN_PATH, ARCHETYPE_STD_PATH
from UHI.modelInfo import settingInfo

ROOT = Path(__file__).resolve().parent.parent
TYPES = ('default', 'residential', 'office', 'commercial', 'hotel', 'medical', 'school')
COLORS = dict(zip(TYPES, ('#a1a8ad', '#dba75b', '#668fc2', '#b779a0', '#8b87bd', '#d57e78', '#71a895')))
SCHEDULE_FIELDS = ('OccupantDensity', 'FreshAirPerPerson', 'OccupantHeatGain', 'EquipmentHeatGain',
                   'LightingHeatGain', 'HeatingSetpointTemp', 'CoolingSetpointTemp', 'CoolingSetpointHumidity')
FIELDS = ('wwr', 'StoryHeight', 'WallUValue', 'WindowUValue', 'WindowSHGC', 'CoolingSetpointTemp',
          'CoolingSetpointHumidity', 'HeatingSetpointTemp', 'CoolingEER', 'HeatingEER',
          'OccupancyStartHour', 'OccupancyEndHour', 'OccupantDensity', 'FreshAirPerPerson',
          'OccupantHeatGain', 'EquipmentHeatGain', 'LightingHeatGain', 'InfiltrationACH',
          'NightVentilationACH', 'WallReflectance', 'RoofReflectance', 'WindowReflectance',
          'WindowTransmittance', 'WallEmissivity', 'RoofEmissivity', 'WindowEmissivity')
UNITS = {'wwr':'-', 'StoryHeight':'m', 'WallUValue':'W/(m²·K)', 'WindowUValue':'W/(m²·K)',
         'CoolingSetpointTemp':'°C', 'HeatingSetpointTemp':'°C', 'CoolingSetpointHumidity':'0–1',
         'OccupancyStartHour':'h', 'OccupancyEndHour':'h', 'OccupantDensity':'人/m²',
         'FreshAirPerPerson':'m³/(h·人)', 'OccupantHeatGain':'W/人', 'EquipmentHeatGain':'W/m²',
         'LightingHeatGain':'W/m²', 'InfiltrationACH':'h⁻¹', 'NightVentilationACH':'h⁻¹'}
LABELS = {'wwr':'窗墙比', 'StoryHeight':'层高', 'WallUValue':'外墙传热系数', 'WindowUValue':'窗传热系数',
          'WindowSHGC':'窗 SHGC（兼容参数）', 'CoolingSetpointTemp':'制冷设定温度', 'CoolingSetpointHumidity':'制冷设定湿度',
          'HeatingSetpointTemp':'供暖设定温度', 'CoolingEER':'制冷 COP', 'HeatingEER':'供暖 COP',
          'OccupancyStartHour':'使用开始时间', 'OccupancyEndHour':'使用结束时间', 'OccupantDensity':'人员密度',
          'FreshAirPerPerson':'人均新风量', 'OccupantHeatGain':'人员散热', 'EquipmentHeatGain':'设备散热',
          'LightingHeatGain':'照明功率', 'InfiltrationACH':'使用时段换气', 'NightVentilationACH':'非使用时段换气',
          'WallReflectance':'墙短波反射率', 'RoofReflectance':'屋顶短波反射率', 'WindowReflectance':'窗短波反射率',
          'WindowTransmittance':'窗短波透射率', 'WallEmissivity':'墙长波发射率', 'RoofEmissivity':'屋顶长波发射率', 'WindowEmissivity':'窗长波发射率'}
FRACTIONS = {'wwr', 'WindowSHGC', 'CoolingSetpointHumidity', 'WallReflectance', 'RoofReflectance',
             'WindowReflectance', 'WindowTransmittance', 'WallEmissivity', 'RoofEmissivity', 'WindowEmissivity'}
INTEGER = {'OccupancyStartHour', 'OccupancyEndHour'}


def number(value, name):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{name} 必须是数值') from None
    if not math.isfinite(result):
        raise ValueError(f'{name} 必须是有限数值')
    return result


def daily(building, key):
    value = getattr(building, key)
    if isinstance(value, str):
        if building.schedule is None or value not in building.schedule.entries:
            raise ValueError(f'日程缺失：{key}')
        return np.asarray(building.schedule.entries[value].hourly_values).reshape(-1, 24).mean(axis=0).tolist()
    return number(value, key)


def set_daily(building, key, values):
    if building.schedule is None:
        building.schedule = Schedule()
    name = f'VIEW_{key}_Daily'
    building.schedule.merge_text(name + ',Daily,' + ','.join(str(float(v)) for v in values))
    setattr(building, key, name)


class MeanRandom(random.Random):
    def normalvariate(self, mu, sigma):
        return mu


def templates(zone=2):
    zone = int(zone or 2)
    with ARCHETYPE_STD_PATH.open(encoding='utf-8-sig', newline='') as stream:
        std_rows = list(csv.DictReader(stream))
    result = {}
    for kind in TYPES[1:]:
        b = BuildingInstance().fromArchetype(zone, kind, rng=MeanRandom())
        std = b._select_archetype_row(std_rows, climate_key=f'climatezone{zone}',
                                      template_type=TEMPLATE_TYPE_MAP[kind], standard='GB55015-2021')
        reverse = {v:k for k,v in b.TEMPLATE_FIELD_MAP.items()}
        values = {key:daily(b,key) for key in FIELDS}
        values['StoryHeight'] = 4.
        uncertainty = {key:float(std.get('wwr' if key == 'wwr' else reverse.get(key,''), 0)) for key in FIELDS}
        result[kind] = dict(values=values, uncertainty=uncertainty)
    result['default'] = dict(
        values={k:(np.mean([r['values'][k] if isinstance(r['values'][k], list) else [r['values'][k]]*24
                            for r in result.values()], axis=0).tolist()
                   if any(isinstance(r['values'][k],list) for r in result.values()) else
                   float(np.mean([r['values'][k] for r in result.values()]))) for k in FIELDS},
        uncertainty={k:float(np.mean([r['uncertainty'][k] for r in result.values()])) for k in FIELDS})
    for template in result.values():
        for key in INTEGER:template['values'][key]=int(round(template['values'][key]))
    return result


def validate_values(values, uncertainty):
    if set(values) - set(FIELDS) or set(uncertainty) - set(FIELDS):
        raise ValueError('存在不支持的建筑参数')
    cleaned = {}
    for k, v in values.items():
        if isinstance(v, list):
            if k not in SCHEDULE_FIELDS or len(v) != 24:
                raise ValueError(f'{k} 日程必须恰好包含 24 个小时值')
            v = [number(x,k) for x in v]
            seq = v
        else:
            v = number(v,k)
            seq = [v]
        for x in seq:
            if k in FRACTIONS and not 0 <= x <= 1:
                raise ValueError(f'{k} 必须在 0–1 之间')
            if k in INTEGER and (x != int(x) or not 0 <= x <= 23):
                raise ValueError(f'{k} 必须是 0–23 的整数')
            if k in {'StoryHeight','WallUValue','WindowUValue','CoolingEER','HeatingEER'} and x <= 0:
                raise ValueError(f'{k} 必须大于 0')
            if k not in {'CoolingSetpointTemp','HeatingSetpointTemp'} and x < 0:
                raise ValueError(f'{k} 不得为负值')
        cleaned[k] = v
    std = {k:number(v,k+' uncertainty') for k,v in uncertainty.items()}
    if any(x < 0 for x in std.values()):
        raise ValueError('标准差不得为负数')
    return cleaned, std


def apply_values(building, values, uncertainty, rng):
    values, uncertainty = validate_values(values, uncertainty)
    for key, value in values.items():
        sigma = uncertainty.get(key, 0)
        if isinstance(value, list):
            scale = max(0, rng.normalvariate(1, sigma)) if sigma else 1
            seq = np.asarray(value)*scale
            if key in FRACTIONS:seq=np.clip(seq,0,1)
            set_daily(building,key,seq)
        else:
            sampled = rng.normalvariate(value,sigma) if sigma else value
            if key in FRACTIONS:
                if not 0<=sampled<=1:sampled=value
                sampled=min(1,max(0,sampled))
            elif key not in {'CoolingSetpointTemp','HeatingSetpointTemp'} and sampled<0:sampled=value
            if key in {'StoryHeight','WallUValue','WindowUValue','CoolingEER','HeatingEER'} and sampled<=0:sampled=value
            if key in INTEGER:sampled=int(min(23,max(0,round(sampled))))
            setattr(building,key,sampled)
    if 'StoryHeight' in values:
        building.multipler=building.height/building.StoryHeight
        building.GFA=building.FloorArea*building.multipler
    building._update_envelope_by_wwr()
    if building.WindowReflectance+building.WindowTransmittance>1+1e-12:
        raise ValueError('窗反射率与透射率之和不得大于 1')
    # Validate the final realized model, including setpoint schedules.
    cool=np.asarray(daily(building,'CoolingSetpointTemp'))
    heat=np.asarray(daily(building,'HeatingSetpointTemp'))
    if np.any(heat>cool):raise ValueError('供暖设定温度不得高于制冷设定温度')


def fresh_state():
    return dict(version=1, selected=[], building_parameters={}, lcz='LCZ5', detected_lcz=None,
                random_ratio=.3, seed=42, sample=.3, iteration='x', load_tolerance=.1,
                start_date='08-20',end_date='08-20', climate_zone=2, weather_loaded=False)


class Editor:
    def __init__(self, data_dir=None):
        self.data_dir=Path(data_dir or ROOT/'outputs'/'view')
        self.data_dir.mkdir(parents=True,exist_ok=True)
        self.n=Neighborhood()
        self.n.view_state=fresh_state()
        self.geometries=[]
        self.project_path=None

    @property
    def state(self):return self.n.view_state

    def invalidate(self):
        self.n._rad=None
        self.n._radiation_network=None
        self.n._mtx=None
        self.n.building_result_file=None

    def import_shp(self, path, height_field=None):
        source=Path(path).expanduser().resolve()
        gdf=gpd.read_file(source)
        candidates=['height','Height','HEIGHT','building_h','bldgheight','高度','建筑高度','楼高']
        if height_field is None:
            height_field=next((k for k in candidates if k in gdf.columns),None)
            if height_field is None:
                return dict(needs_height=True,columns=[str(c) for c in gdf.columns if c!=gdf.geometry.name])
        if height_field not in gdf.columns:raise ValueError(f'高度列不存在：{height_field}，已取消导入')
        heights=gdf[height_field].to_numpy(dtype=float)
        if not len(gdf) or not np.isfinite(heights).all() or np.any(heights<=0):
            raise ValueError('高度列必须全部为大于 0 的米制数值，已取消导入')
        if gdf.crs is None:raise ValueError('SHP 缺少坐标系，无法确定建筑面积及高度阴影')
        if gdf.geometry.isna().any() or gdf.geometry.is_empty.any() or not gdf.geometry.is_valid.all():
            raise ValueError('SHP 包含空或无效几何，已取消导入')
        if not gdf.geom_type.isin(['Polygon','MultiPolygon']).all():raise ValueError('只支持建筑 Polygon / MultiPolygon')
        projected=gdf.crs.is_projected and all(a.unit_name.lower() in ('metre','meter') for a in gdf.crs.axis_info[:2])
        if not projected:gdf=gdf.to_crs(gdf.estimate_utm_crs())
        # Use normalized, metric geometry for BOTH the map and RAD inventory.
        dest=self.data_dir/'imports'/next(tempfile._get_candidate_names())/'buildings.shp'
        dest.parent.mkdir(parents=True,exist_ok=True)
        normalized=gpd.GeoDataFrame({'height':heights},geometry=gdf.geometry.reset_index(drop=True),crs=gdf.crs)
        normalized.to_file(dest,encoding='UTF-8')
        candidate=copy.deepcopy(self.n)
        candidate.readSHP(dest,height_field='height',fvc=.2,fwc=.05)
        detected=candidate.site.LCZ
        label=detected if (settingInfo.params_dir/f'{detected}.json').is_file() else 'LCZ5'
        candidate.setting=settingInfo.loadJSON(settingInfo.params_dir/f'{label}.json')
        candidate.view_state.update(selected=[],building_parameters={},lcz=label,detected_lcz=detected)
        template=templates(candidate.location.climate_zone or self.state['climate_zone'])['default']
        for b in candidate.buildings:
            apply_values(b,template['values'],{k:0 for k in FIELDS},random.Random(42))
            b.archetype='default'
            candidate.view_state['building_parameters'][b.building_id]=copy.deepcopy(template)
        self.n=candidate
        self.geometries=list(normalized.geometry)
        self.invalidate()
        return dict(needs_height=False,detected_lcz=detected,applied_lcz=label)

    def import_weather(self,path,start,end,zone=None):
        start_hoy,end_hoy=date_window(start,end)
        candidate=copy.deepcopy(self.n)
        site=copy.deepcopy(candidate.site)
        candidate.readEPW(path,start_hoy=start_hoy,end_hoy=end_hoy,climate_zone=zone,smooth_weather=False)
        if self.n.buildings:candidate.site=site
        actual_zone=candidate.location.climate_zone or int(zone or self.state['climate_zone'])
        candidate.view_state.update(start_date=start,end_date=end,climate_zone=actual_zone,weather_loaded=True)
        # Only untouched default buildings follow a newly imported climate zone.
        if actual_zone!=(self.n.location.climate_zone or self.state['climate_zone']):
            template=templates(actual_zone)['default']
            for b in candidate.buildings:
                entry=candidate.view_state['building_parameters'].get(b.building_id,{})
                if b.archetype=='default' and not entry.get('edited',False):
                    apply_values(b,template['values'],{k:0 for k in FIELDS},random.Random(42))
                    candidate.view_state['building_parameters'][b.building_id]=copy.deepcopy(template)
        self.n=candidate
        self.invalidate()

    def apply_buildings(self,ids,values,uncertainty,kind=None):
        if not ids:raise ValueError('请先选择建筑')
        inventory={b.building_id:b for b in self.n.buildings}
        if set(ids)-inventory.keys():raise ValueError('选择集包含不存在的建筑')
        if kind is not None and kind not in TYPES:raise ValueError('未知建筑类型')
        # A std-only edit must resample the building's existing mean/value,
        # rather than silently changing only editor metadata.
        validate_values(values,uncertainty)
        candidate=copy.deepcopy(self.n)
        rng=random.Random(int(self.state['seed']))
        for b in candidate.buildings:
            if b.building_id not in ids:continue
            existing=candidate.view_state['building_parameters'].get(b.building_id,{})
            effective=dict(values)
            for key in uncertainty:
                if key not in effective:effective[key]=existing.get('values',{}).get(key,daily(b,key))
            apply_values(b,effective,uncertainty,rng)
            if kind:b.archetype=kind
            params=candidate.view_state['building_parameters'].setdefault(b.building_id,dict(values={},uncertainty={}))
            params['values'].update(copy.deepcopy(values))
            params['uncertainty'].update(copy.deepcopy(uncertainty))
            params['edited']=True
        candidate.syncSiteGFA()
        self.n=candidate
        self.invalidate()

    def urban(self):
        site=self.n.site
        return dict(setting=self.n.setting.to_dict(), site={k:getattr(site,k) for k in
                    ('SVF','kS','greenRoofPercentage','SDG','SRTL','SRTG','fPSA','CL','CG')},
                    FVC=site.AreaLandscape/site.TotalArea, FWC=site.AreaWater/site.TotalArea)

    def apply_urban(self,data,lcz='LCZx'):
        candidate=copy.deepcopy(self.n)
        if lcz!='LCZx':
            if lcz not in ('LCZ1','LCZ2','LCZ3','LCZ4','LCZ5'):raise ValueError('未知 LCZ 模板')
            candidate.setting=settingInfo.loadJSON(settingInfo.params_dir/f'{lcz}.json')
        else:
            for key,value in data.get('setting',{}).items():
                if key not in settingInfo.__slots__:raise ValueError(f'未知城市参数：{key}')
                old=getattr(candidate.setting,key)
                if isinstance(old,(list,np.ndarray)):
                    if not isinstance(value,list) or len(value)!=len(old):raise ValueError(f'{key} 数组长度无效')
                    value=[number(x,key) for x in value]
                elif isinstance(old,(bool,np.bool_)):
                    if not isinstance(value,bool):raise ValueError(f'{key} 必须是布尔值')
                elif isinstance(old,str):value=str(value)
                elif value is None and key=='dt':pass
                else:value=number(value,key)
                setattr(candidate.setting,key,value)
            allowed=set(self.urban()['site'])
            for key,value in data.get('site',{}).items():
                if key not in allowed:raise ValueError('未知场地参数')
                value=number(value,key)
                if not 0<=value<=1:raise ValueError(f'{key} 必须在 0–1 之间')
                setattr(candidate.site,key,value)
            fvc=number(data.get('FVC',self.urban()['FVC']),'FVC')
            fwc=number(data.get('FWC',self.urban()['FWC']),'FWC')
            s=candidate.site
            remaining=s.TotalArea-s.AreaBuilding-s.TotalArea*(fvc+fwc)
            if min(fvc,fwc)<0 or max(fvc,fwc)>1 or remaining<0:
                raise ValueError('FVC + FWC 与建筑覆盖率之和不得大于 1')
            ratio=s.AreaRoad/max(s.AreaRoad+s.AreaGround,1e-12)
            s.AreaLandscape=fvc*s.TotalArea;s.AreaWater=fwc*s.TotalArea
            s.AreaRoad=remaining*ratio;s.AreaGround=remaining*(1-ratio)
            s.AreaTreeShading=s.AreaLandscape;s.SDL=fvc
        candidate.view_state['lcz']=lcz
        self.n=candidate
        self.invalidate()

    def remember_urban(self):
        if self.state['lcz']!='LCZx':return
        folder=self.data_dir/'params';folder.mkdir(exist_ok=True)
        record=dict(timestamp=datetime.now().isoformat(),**self.urban())
        write_json(folder/(datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.json'),record)
        write_json(folder/'LCZx.json',record)

    def restore_urban(self):
        path=self.data_dir/'params'/'LCZx.json'
        if not path.is_file():raise ValueError('还没有保存过 LCZx 计算设置')
        self.apply_urban(json.loads(path.read_text(encoding='utf-8')))

    def snapshot(self):
        features=[]
        from shapely.geometry import mapping
        for geometry,b in zip(self.geometries,self.n.buildings):
            features.append(dict(type='Feature',geometry=mapping(geometry),properties=dict(
                id=b.building_id,height=b.height,kind=b.archetype if b.archetype in TYPES else 'default',gfa=b.GFA)))
        return dict(state=self.state,urban=self.urban(),geometry=dict(type='FeatureCollection',features=features),
                    location=dict(latitude=self.n.location.latitude,longitude=self.n.location.longitude,
                                  climate_zone=self.n.location.climate_zone),
                    templates=templates(self.n.location.climate_zone or self.state['climate_zone']),
                    buildings=[dict(id=b.building_id,kind=b.archetype,values={k:daily(b,k) for k in FIELDS},
                         parameters=self.state['building_parameters'].get(b.building_id,{})) for b in self.n.buildings],
                    paths=dict(shp=self.n._shp,weather=self.n._epw or self.n._weather_csv,rad=self.n._rad,
                               project=str(self.project_path) if self.project_path else None),
                    fields=[dict(key=k,label=LABELS.get(k,k),unit=UNITS.get(k,'-'),schedule=k in SCHEDULE_FIELDS) for k in FIELDS],
                    colors=COLORS,total_gfa=sum(b.GFA for b in self.n.buildings))

    def save(self,path):
        target=Path(path).expanduser().resolve()
        if target.suffix.lower()!='.json':raise ValueError('Neighborhood 文件必须以 .json 结尾')
        target.parent.mkdir(parents=True,exist_ok=True)
        data=self.n.toDict()
        for key in ('_shp','_epw','_weather_csv','_mtx','_rad','building_result_file'):
            value=data.get(key)
            if not value:continue
            source=Path(value).resolve()
            if not source.is_file():raise ValueError(f'依赖文件不存在：{source}')
            files=list(source.parent.glob(source.stem+'.*')) if key=='_shp' else [source]
            # Prefix avoids collisions without placing assets in a separate directory.
            stem=f'{target.stem}_{key.lstrip("_")}'
            main=None
            for item in files:
                suffix=item.name[len(source.stem):] if key=='_shp' else ''.join(source.suffixes)
                destination=target.parent/(stem+suffix)
                if item.resolve()!=destination.resolve():shutil.copy2(item,destination)
                if item==source:main=destination
            data[key]=main.name
        write_json(target,data)
        self.project_path=target
        return target

    def load(self,path):
        source=Path(path).expanduser().resolve()
        candidate=Neighborhood.loadJSON(source)
        metadata=candidate.view_state
        valid=isinstance(metadata,dict) and metadata.get('version')==1
        state=fresh_state()
        if valid:
            for key in state:
                if key in metadata and isinstance(metadata[key],type(state[key])):state[key]=copy.deepcopy(metadata[key])
        ids={b.building_id for b in candidate.buildings}
        state['selected']=[x for x in state['selected'] if x in ids]
        state['building_parameters']={k:v for k,v in state['building_parameters'].items() if k in ids and isinstance(v,dict)}
        state['weather_loaded']=bool(candidate.climate and (candidate._epw or candidate._weather_csv))
        state['climate_zone']=candidate.location.climate_zone or state['climate_zone']
        if not valid:
            state['start_date']=hoy_date(candidate._start_hoy)
            state['end_date']=hoy_date(candidate._end_hoy-1)
            # A custom parameter set must not be mislabeled as a standard LCZ.
            state['lcz']='LCZx'
        candidate.view_state=state
        geometries=[]
        if candidate._shp and Path(candidate._shp).is_file():
            gdf=gpd.read_file(candidate._shp)
            gdf=gdf[gdf.geometry.notnull() & (gdf[candidate._height_field].astype(float)>0)]
            if len(gdf)!=len(candidate.buildings):raise ValueError('SHP 与 Neighborhood 建筑数量不一致')
            geometries=list(gdf.geometry)
        self.n=candidate;self.geometries=geometries;self.project_path=source


def date_window(start,end):
    def parse(value):
        try:return datetime.strptime('2025-'+value,'%Y-%m-%d')
        except ValueError:raise ValueError('日期请使用 MM-DD，非闰年 01-01 至 12-31') from None
    a,b=parse(start),parse(end)
    if b<a:raise ValueError('结束日期不得早于开始日期')
    first=datetime(2025,1,1)
    return (a-first).days*24,((b-first).days+1)*24


def hoy_date(hoy):
    from datetime import timedelta
    return (datetime(2025,1,1)+timedelta(hours=int(hoy))).strftime('%m-%d')


def write_json(path,data):
    path=Path(path);tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,allow_nan=False,indent=2),encoding='utf-8')
    tmp.replace(path)
