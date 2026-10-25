"""CETUS 1.9: geometry-free, threshold-driven daylight/lighting estimator.

The existing RAD shortwave and reflected-shortwave outputs remain in W/m2.
This module uses a *declared, approximate* visible energy fraction and a
photopic luminous efficacy to obtain photometric facade/roof illuminances.
It does not claim that a broadband RAD result contains measured spectral data.

Each floor can be split into perimeter/core lighting controls. Optional room
metadata need only areas and window access, never polygons or thermal zones.
The original energy thermal model remains one zone per building.

IMPORTANT: coefficient/utilization defaults are uncalibrated priors intended
for integration tests and sensitivity analysis, not verified universal values.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence
import math
import numpy as np


@dataclass(frozen=True)
class SpectralFractions:
    visible: float = 0.43
    near_infrared: float = 0.52
    other: float = 0.05  # mostly UV / residual shortwave; not photopic

    def validate(self) -> None:
        vals = (self.visible, self.near_infrared, self.other)
        if not all(math.isfinite(x) and 0 <= x <= 1 for x in vals):
            raise ValueError('Spectral fractions must be finite numbers between 0 and 1')
        if not math.isclose(sum(vals), 1.0, rel_tol=0, abs_tol=1e-9):
            raise ValueError('Spectral fractions must sum to exactly one')


@dataclass(frozen=True)
class SpectralWindow:
    visible_transmittance: float
    near_infrared_transmittance: float
    other_transmittance: float = 0.0
    fractions: SpectralFractions = field(default_factory=SpectralFractions)

    def __post_init__(self):
        self.fractions.validate()
        for x in (self.visible_transmittance, self.near_infrared_transmittance, self.other_transmittance):
            if not math.isfinite(x) or x < 0 or x > 1:
                raise ValueError('All spectral transmittances must lie in [0,1]')

    @property
    def broadband_solar_transmittance(self) -> float:
        """Energy-weighted approximation used by the *thermal* solar channel."""
        f = self.fractions
        return (f.visible * self.visible_transmittance
                + f.near_infrared * self.near_infrared_transmittance
                + f.other * self.other_transmittance)


@dataclass(frozen=True)
class DaylightConfig:
    threshold_lux: float = 300.0
    luminous_efficacy_lm_per_visible_w: float = 250.0
    facade_utilization: float = 0.22
    skylight_utilization: float = 0.35
    controlled_fraction: float = 1.0
    visible_reflected_fraction: float | None = None

    def validate(self):
        if not math.isfinite(self.threshold_lux) or self.threshold_lux <= 0:
            raise ValueError('threshold_lux must be positive')
        if not math.isfinite(self.luminous_efficacy_lm_per_visible_w) or self.luminous_efficacy_lm_per_visible_w <= 0:
            raise ValueError('Luminous efficacy must be positive')
        for name in ('facade_utilization','skylight_utilization','controlled_fraction'):
            x = getattr(self, name)
            if not math.isfinite(x) or not 0 <= x <= 1:
                raise ValueError(f'{name} must be a dimensionless fraction in [0,1]')
        x = self.visible_reflected_fraction
        if x is not None and (not math.isfinite(x) or not 0 <= x <= 1):
            raise ValueError('visible_reflected_fraction must be in [0,1]')


@dataclass
class LightingResult:
    baseline_lighting_w: np.ndarray
    lighting_w: np.ndarray
    avoided_lighting_w: np.ndarray
    lighting_schedule_ratio: np.ndarray
    baseline_lighting_w_m2: np.ndarray
    lighting_w_m2: np.ndarray
    facade_illuminance_lux: np.ndarray
    roof_illuminance_lux: np.ndarray
    visible_facade_irradiance_w_m2: np.ndarray
    near_infrared_facade_irradiance_w_m2: np.ndarray
    visible_roof_irradiance_w_m2: np.ndarray
    near_infrared_roof_irradiance_w_m2: np.ndarray
    transmitted_visible_facade_w_m2: np.ndarray
    transmitted_nir_facade_w_m2: np.ndarray
    illuminance_lux_by_group: dict[str, np.ndarray]
    lights_on_by_group: dict[str, np.ndarray]
    group_areas_m2: dict[str, float]
    notes: list[str]

    def audit(self, *, include_groups: bool = True) -> dict[str, Any]:
        out = {
            'baseline_lighting_w': self.baseline_lighting_w.tolist(),
            'lighting_electricity_w': self.lighting_w.tolist(),
            'avoided_lighting_w': self.avoided_lighting_w.tolist(),
            'lighting_schedule_ratio': self.lighting_schedule_ratio.tolist(),
            'lighting_w_m2': self.lighting_w_m2.tolist(),
            'facade_illuminance_lux': self.facade_illuminance_lux.tolist(),
            'roof_illuminance_lux': self.roof_illuminance_lux.tolist(),
            'visible_facade_irradiance_w_m2': self.visible_facade_irradiance_w_m2.tolist(),
            'near_infrared_facade_irradiance_w_m2': self.near_infrared_facade_irradiance_w_m2.tolist(),
            'visible_roof_irradiance_w_m2': self.visible_roof_irradiance_w_m2.tolist(),
            'near_infrared_roof_irradiance_w_m2': self.near_infrared_roof_irradiance_w_m2.tolist(),
            'transmitted_visible_facade_w_m2': self.transmitted_visible_facade_w_m2.tolist(),
            'transmitted_nir_facade_w_m2': self.transmitted_nir_facade_w_m2.tolist(),
            'notes': self.notes,
        }
        if include_groups:
            out['illuminance_lux_by_group'] = {k:v.tolist() for k,v in self.illuminance_lux_by_group.items()}
            out['lights_on_by_group'] = {k:v.astype(int).tolist() for k,v in self.lights_on_by_group.items()}
            out['group_areas_m2'] = dict(self.group_areas_m2)
        return out


def _array(values: Sequence[float] | np.ndarray, name: str, n: int | None = None) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    if arr.ndim != 1 or (n is not None and len(arr) != n) or not np.isfinite(arr).all() or np.any(arr < 0):
        raise ValueError(f'{name} must be a finite, nonnegative one-dimensional vector of length {n}')
    return arr


def _positive(x: float, name: str) -> float:
    v = float(x)
    if not math.isfinite(v) or v <= 0:
        raise ValueError(f'{name} must be positive')
    return v


def floor_areas(typical_floor_area_m2: float, gross_floor_area_m2: float) -> list[float]:
    """Partition GFA into full typical floors and a possible partial top floor."""
    a = _positive(typical_floor_area_m2, 'typical_floor_area_m2')
    total = _positive(gross_floor_area_m2, 'gross_floor_area_m2')
    n = int(total // a)
    result = [a] * n
    rem = total - n*a
    if rem > 1e-7*a:
        result.append(rem)
    return result or [total]


def _allocate_capped(total: float, weights: np.ndarray, capacity: np.ndarray) -> np.ndarray:
    """Nonnegative weighted water filling subject to per-room area capacities."""
    out = np.zeros(len(capacity), dtype=float)
    remaining = max(0.0, min(total, float(np.sum(capacity))))
    available = capacity.copy()
    for _ in range(len(capacity) + 1):
        active = np.where(available > 1e-10)[0]
        if len(active) == 0 or remaining <= 1e-10:
            break
        ww = np.maximum(weights[active], 0)
        if ww.sum() == 0:
            ww = available[active]
        proposal = remaining*ww/ww.sum()
        allocated = np.minimum(proposal, available[active])
        out[active] += allocated
        available[active] -= allocated
        remaining -= float(allocated.sum())
    return out


def estimate_daylighting(
    *,
    facade_shortwave_w_m2: Sequence[float],
    roof_shortwave_w_m2: Sequence[float],
    baseline_lighting_w_m2: Sequence[float],
    typical_floor_area_m2: float,
    gross_floor_area_m2: float,
    perimeter_zone_area_m2: float,
    facade_window_area_m2: float,
    skylight_area_m2: float = 0.0,
    glazing: SpectralWindow,
    config: DaylightConfig = DaylightConfig(),
    facade_reflected_w_m2: Sequence[float] | None = None,
    roof_reflected_w_m2: Sequence[float] | None = None,
    rooms_by_floor: Mapping[int, Sequence[Mapping[str, Any]]] | None = None,
) -> LightingResult:
    """Estimate effective workplane lux and on/off lighting for a thermal single zone.

    Incoming shortwave is broadband W/m2 on facade/roof planes. Optional
    reflected input is *additive*, not already included in the first argument.
    The same illuminance is applied to each window on the same plane; when
    facade directions differ, caller should supply a suitable area-weighted
    window irradiance (or use the more detailed per-window API in the future).
    Rooms are optional area-only metadata keyed by zero-based floor index.
    An absent window designation does NOT pretend to identify windowed rooms:
    it falls back to floor perimeter/core proxies and records this limitation.
    """
    config.validate()
    a_typ = _positive(typical_floor_area_m2, 'typical_floor_area_m2')
    gross = _positive(gross_floor_area_m2, 'gross_floor_area_m2')
    pza = float(perimeter_zone_area_m2)
    aw = float(facade_window_area_m2)
    asky = float(skylight_area_m2)
    if not all(math.isfinite(v) and v >= 0 for v in (pza, aw, asky)):
        raise ValueError('Perimeter area and glazing areas must be nonnegative')
    if pza > a_typ + 1e-9:
        raise ValueError('PerimeterZoneArea must not exceed typical floor area')
    primary_facade = _array(facade_shortwave_w_m2, 'facade_shortwave_w_m2')
    n = len(primary_facade)
    primary_roof = _array(roof_shortwave_w_m2, 'roof_shortwave_w_m2', n)
    base = _array(baseline_lighting_w_m2, 'baseline_lighting_w_m2', n)
    reflected_facade = (_array(facade_reflected_w_m2,'facade_reflected_w_m2',n)
                        if facade_reflected_w_m2 is not None else np.zeros(n))
    reflected_roof = (_array(roof_reflected_w_m2,'roof_reflected_w_m2',n)
                      if roof_reflected_w_m2 is not None else np.zeros(n))
    f = glazing.fractions
    r = config.visible_reflected_fraction
    r = f.visible if r is None else r
    # Optional reflected-band adjustment must still preserve total shortwave
    # energy; redistribute the non-visible remainder in the original NIR/UV ratio.
    nonvisible = f.near_infrared + f.other
    reflected_nir_fraction = (1-r)*f.near_infrared/nonvisible if nonvisible else 0.0
    facade_vis = f.visible*primary_facade + r*reflected_facade
    facade_nir = f.near_infrared*primary_facade + reflected_nir_fraction*reflected_facade
    roof_vis = f.visible*primary_roof + r*reflected_roof
    roof_nir = f.near_infrared*primary_roof + reflected_nir_fraction*reflected_roof
    k = config.luminous_efficacy_lm_per_visible_w
    Efacade = facade_vis*k  # lux on window plane before glazing
    Eroof = roof_vis*k
    floors = floor_areas(a_typ, gross)
    n_floors = len(floors)
    group_area: dict[str,float] = {}
    group_E: dict[str,np.ndarray] = {}
    group_on: dict[str,np.ndarray] = {}
    notes: list[str] = []
    pfrac = pza/a_typ
    if aw <= 0 and asky <= 0:
        notes.append('No external glazing: lighting remains at the original baseline schedule.')
    notes.append('Broadband-to-visible and luminous efficacy are assumed spectral/photometric priors, not measured spectra.')
    notes.append('Workplane lux is a room/perimeter effective mean; geometry, glare, blinds and daylight nonuniformity are not resolved.')

    def add_group(name: str, area: float, lux: np.ndarray):
        if area <= 1e-10:
            return
        if name in group_area:
            raise ValueError(f'Duplicate lighting group name: {name}')
        group_area[name] = float(area)
        E = np.maximum(0.0, lux)
        group_E[name] = E
        group_on[name] = E < config.threshold_lux

    for fi, A in enumerate(floors):
        A_per = min(A, A*pfrac)
        window_area_floor = aw*A/gross  # explicit uniform facade allocation prior
        skylight_floor = asky if fi == n_floors-1 else 0.0
        roof_lux = (Eroof*glazing.visible_transmittance
                    * skylight_floor*config.skylight_utilization/A)
        room_list = None if rooms_by_floor is None else (rooms_by_floor.get(fi) or rooms_by_floor.get(str(fi)))
        if room_list:
            rooms = [dict(x) for x in room_list]
            sizes = np.array([float(x['area_m2']) for x in rooms],dtype=float)
            if np.any(~np.isfinite(sizes)) or np.any(sizes<=0) or sizes.sum() > A+1e-7:
                raise ValueError(f'Room areas on floor {fi} must be positive and sum to at most floor area')
            explicit = any('has_window' in x or 'window_area_m2' in x or 'skylight_area_m2' in x for x in rooms)
            if explicit:
                windowed = np.array([bool(x.get('has_window',float(x.get('window_area_m2',0.0))>0)) for x in rooms])
                window_alloc = np.array([float(x.get('window_area_m2',0.0)) for x in rooms])
                if np.any(window_alloc<0) or np.any(~np.isfinite(window_alloc)) or np.any(window_alloc[~windowed]>0):
                    raise ValueError('Room window areas inconsistent with has_window')
                if window_alloc.sum() > window_area_floor+1e-7:
                    raise ValueError(f'Assigned room window area exceeds floor {fi} glazing area')
                unallocated = max(0,window_area_floor-window_alloc.sum())
                eligible = np.where(windowed & (window_alloc==0))[0]
                if eligible.size and unallocated>0:
                    window_alloc[eligible] += unallocated*sizes[eligible]/sizes[eligible].sum()
                elif unallocated>1e-8:
                    notes.append(f'Floor {fi}: some facade glazing unassigned to rooms; omitted from room daylight calculation.')
                sky_alloc = np.array([float(x.get('skylight_area_m2',0.0)) for x in rooms])
                if np.any(~np.isfinite(sky_alloc)) or np.any(sky_alloc<0) or sky_alloc.sum()>skylight_floor+1e-7:
                    raise ValueError(f'Assigned room skylight area exceeds floor {fi} skylight area')
                if skylight_floor-sky_alloc.sum()>1e-8:
                    notes.append(f'Floor {fi}: unassigned skylights excluded from room daylight estimates; avoid illuminating enclosed rooms.')
                near = _allocate_capped(A_per,window_alloc,np.where(windowed,sizes,0))
                for ri, (s, a_day, a_win) in enumerate(zip(sizes,near,window_alloc)):
                    label = str(rooms[ri].get('name',f'room_{ri+1}'))
                    room_roof_lux = Eroof*glazing.visible_transmittance*config.skylight_utilization*sky_alloc[ri]/s
                    # Optional room/window-specific incident W/m2 can be
                    # supplied without any room polygons or 3D geometry.
                    room_primary = _array(rooms[ri].get('facade_shortwave_w_m2',primary_facade),
                                          f'floor_{fi+1}/{label} facade shortwave',n)
                    room_reflected = _array(rooms[ri].get('facade_reflected_w_m2',reflected_facade),
                                            f'floor_{fi+1}/{label} facade reflected',n)
                    room_window_lux = k*(f.visible*room_primary+r*room_reflected)
                    e_room = room_window_lux*glazing.visible_transmittance*config.facade_utilization*a_win/max(a_day,1e-12)
                    add_group(f'floor_{fi+1}:{label}:perimeter',a_day,e_room+room_roof_lux)
                    add_group(f'floor_{fi+1}:{label}:core',s-a_day,room_roof_lux)
                if sizes.sum()<A-1e-7:
                    add_group(f'floor_{fi+1}:unspecified_area',A-sizes.sum(),np.zeros(n))
                continue
            notes.append(f'Floor {fi}: room area metadata lacked window access; using perimeter/core aggregate instead.')
        facade_day_lux = (Efacade*glazing.visible_transmittance
                          * window_area_floor*config.facade_utilization/max(A_per,1e-12))
        add_group(f'floor_{fi+1}:perimeter',A_per,facade_day_lux+roof_lux)
        add_group(f'floor_{fi+1}:core',A-A_per,roof_lux)

    if not math.isclose(sum(group_area.values()),gross,abs_tol=1e-6,rel_tol=1e-8):
        raise RuntimeError('Lighting control groups do not cover exactly the thermal building GFA')
    original = gross*base
    actual = np.zeros(n,dtype=float)
    for name,A in group_area.items():
        on = group_on[name].astype(float)
        # The uncontrolled part keeps baseline; automated lights follow threshold.
        ratio = (1-config.controlled_fraction)+config.controlled_fraction*on
        actual += base*A*ratio
    ratio = np.divide(actual,original,out=np.ones_like(original),where=original>0)
    if np.any(actual-original>1e-7):
        raise RuntimeError('Automatic control cannot increase the original lighting baseline')
    return LightingResult(original,actual,original-actual,ratio,base,actual/gross,
                          Efacade,Eroof,facade_vis,facade_nir,roof_vis,roof_nir,
                          facade_vis*glazing.visible_transmittance,
                          facade_nir*glazing.near_infrared_transmittance,
                          group_E,group_on,group_area,notes)


def model_from_building(building: Any, climate: Any, irradiance: Mapping[str, Sequence[float]],
                        lighting_w_m2: Sequence[float], options: Mapping[str, Any]) -> LightingResult:
    """Convenience bridge for CETUS's existing single-thermal-zone building object.

    The caller must supply the SAME facade/roof irradiation ultimately used by
    Energy; no implicit re-raytracing or new geometry is performed here.
    """
    from collections.abc import Mapping as MappingABC
    if not isinstance(options,MappingABC):
        raise TypeError('daylighting options must be a mapping')
    enabled = bool(options.get('enabled',False))
    if not enabled:
        raise ValueError('model_from_building requires daylighting enabled')
    glass = glazing_from_building(building, options)
    if glass is None:
        raise ValueError('Specify visible and near-infrared transmittances before enabling daylight control')
    cfg = DaylightConfig(
        threshold_lux=float(options.get('threshold_lux',300.0)),
        luminous_efficacy_lm_per_visible_w=float(options.get('luminous_efficacy_lm_per_visible_w',250)),
        facade_utilization=float(options.get('facade_utilization',.22)),
        skylight_utilization=float(options.get('skylight_utilization',.35)),
        controlled_fraction=float(options.get('controlled_fraction',1.0)),
        visible_reflected_fraction=options.get('visible_reflected_fraction'))
    # Reflected arrays are additive only when their primary partners exist.
    # If totals are supplied too, check closure to prevent double counting.
    for plane in ('facade', 'roof'):
        pk, rk = plane+'_primary', plane+'_reflected'
        if rk in irradiance and pk not in irradiance:
            raise ValueError('Reflected irradiation requires a separate primary array')
        if pk in irradiance and not np.allclose(
                np.asarray(irradiance[pk])+np.asarray(irradiance.get(rk,0)),
                irradiance[plane], rtol=1e-5, atol=1e-4):
            raise ValueError('Primary plus reflected irradiation must equal total')
    irr_f = irradiance.get('facade_primary', irradiance['facade'])
    irr_r = irradiance.get('roof_primary', irradiance['roof'])
    rooms = options.get('rooms_by_floor')
    if rooms is not None and climate is not None:
        from copy import deepcopy
        rooms = deepcopy(rooms)
        for records in rooms.values():
            for room in records:
                for key in ('facade_shortwave_w_m2','facade_reflected_w_m2'):
                    if key in room and len(room[key]) == 8760:
                        room[key] = np.asarray(room[key])[np.asarray(climate.HOY,dtype=int)]
    return estimate_daylighting(
        facade_shortwave_w_m2=irr_f,
        roof_shortwave_w_m2=irr_r,
        facade_reflected_w_m2=irradiance.get('facade_reflected'),
        roof_reflected_w_m2=irradiance.get('roof_reflected'),
        baseline_lighting_w_m2=lighting_w_m2,
        typical_floor_area_m2=float(building.FloorArea),
        gross_floor_area_m2=float(building.GFA),
        perimeter_zone_area_m2=float(building.PerimeterZoneArea),
        facade_window_area_m2=(float(building._perimeter)*float(building.height)*float(building.wwr)
                                  if float(getattr(building,'_perimeter',0.0))>0
                                  else float(getattr(building,'ExteriorWindowArea',0.0))
                                       *float(building.GFA)/float(building.FloorArea)),
        skylight_area_m2=float(getattr(building,'SkylightArea',0.0)),
        glazing=glass,config=cfg,
        rooms_by_floor=rooms,
    )


def glazing_from_building(building: Any, options: Mapping[str, Any]) -> SpectralWindow | None:
    """Material properties apply even with daylight control disabled."""
    allowed = {'enabled','visible_transmittance','near_infrared_transmittance',
        'other_transmittance','visible_fraction','near_infrared_fraction','other_fraction',
        'threshold_lux','luminous_efficacy_lm_per_visible_w','facade_utilization',
        'skylight_utilization','controlled_fraction','visible_reflected_fraction','rooms_by_floor'}
    if set(options)-allowed:
        raise ValueError(f'Unknown daylight options: {sorted(set(options)-allowed)}')
    tv=options.get('visible_transmittance',getattr(building,'WindowVisibleTransmittance',None))
    tn=options.get('near_infrared_transmittance',getattr(building,'WindowNIRTransmittance',None))
    if tv is None and tn is None:
        return None
    if tv is None or tn is None:
        raise ValueError('Visible and near-infrared transmittances must be supplied together')
    fractions=SpectralFractions(float(options.get('visible_fraction',.43)),
        float(options.get('near_infrared_fraction',.52)),float(options.get('other_fraction',.05)))
    return SpectralWindow(float(tv),float(tn),float(options.get('other_transmittance',0)),fractions)
