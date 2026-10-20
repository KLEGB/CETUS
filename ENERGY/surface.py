"""Energy v7 reconstruction: explicit surface optics and immutable hourly states."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import platform
import queue
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

import numpy as np

from .steady import _schedule

ROOT = Path(__file__).resolve().parent
SIGMA = 5.670374419e-8


def ensure_surface_binary(executable_path=None):
    if executable_path is not None:
        target = Path(executable_path).resolve()
        if not target.is_file():
            raise FileNotFoundError(target)
        return target
    target = ROOT / 'bin' / ('CetusSurfaceEnergy.exe' if platform.system() == 'Windows' else 'CetusSurfaceEnergy')
    source = ROOT / 'surface_engine' / 'main.go'
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    stamp = target.with_suffix(target.suffix + '.sha256')
    if not target.exists() or not stamp.exists() or stamp.read_text().strip() != digest:
        compiler = os.environ.get('CETUS_GO') or shutil.which('go')
        if not compiler:
            raise RuntimeError('Install Go 1.22+ or set CETUS_GO to rebuild CetusSurfaceEnergy.')
        subprocess.run([compiler, 'build', '-o', str(target), '.'], cwd=source.parent,
                       check=True, capture_output=True, text=True, timeout=120)
        stamp.write_text(digest + '\n')
    return target


class SurfaceEngine:
    """One persistent process; no temperature state is held by the process."""
    def __init__(self, executable_path=None, timeout=120):
        self.timeout = timeout
        self.errors = tempfile.TemporaryFile(mode='w+t')
        self.process = subprocess.Popen([str(ensure_surface_binary(executable_path))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.errors,
            text=True, encoding='utf-8', bufsize=1,
            env={**os.environ,'GOMAXPROCS':os.environ.get('CETUS_ENERGY_WORKERS','4')})
        self.responses = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        for line in self.process.stdout:
            self.responses.put(line)
        self.responses.put(None)

    def _send(self, payload):
        try:
            self.process.stdin.write(json.dumps(payload, allow_nan=False, separators=(',', ':')) + '\n')
            self.process.stdin.flush()
            line = self.responses.get(timeout=self.timeout)
        except (BrokenPipeError, queue.Empty) as exc:
            self.close()
            raise RuntimeError('Surface engine stopped or timed out') from exc
        if line is None:
            self.process.wait(timeout=10)
            self.errors.seek(0)
            raise RuntimeError('Surface engine: ' + self.errors.read())
        return json.loads(line)

    def configure(self, models):
        return self._send({'models': models})

    def request(self, batch, *, use_models=False):
        payloads=[{k:p[k] for k in ('hours','initial_state') if k in p} for p in batch] if use_models else batch
        return self._send({'batch': payloads,'use_models':use_models})['batch']

    def trial(self, batch):
        payloads=[{k:p[k] for k in ('hours','initial_state') if k in p} for p in batch]
        return self._send({'batch':payloads,'use_models':True,'trial':True})

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        for stream in (self.process.stdin, self.process.stdout, self.errors):
            if not stream.closed:
                stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def build_surface_input(building, climate, *, irradiance=None, options=None):
    """Build a natural day. Options are explicit construction/optical overrides.

    Existing archetypes expose SHGC only. Until true optical data are supplied,
    SHGC is an *effective* transmittance with alpha=0, rho=1-SHGC. This preserves
    its effective solar gain once; it does not claim a measured transmittance.
    """
    options = copy.deepcopy(options or {})
    allowed = {'surfaces', 'internal_radiant_fraction', 'solar_floor_fraction', 'dt_s'}
    if set(options) - allowed:
        raise ValueError(f'Unknown surface options: {sorted(set(options)-allowed)}')
    overrides = options.get('surfaces', {})
    if set(overrides) - {'wall', 'roof', 'floor', 'window', 'skylight'}:
        raise ValueError('Unknown surface override')
    hoy = np.asarray(climate.HOY, dtype=int)
    if hoy.shape != (24,) or not np.array_equal(hoy, np.arange(hoy[0], hoy[0]+24)) or hoy[0] % 24:
        raise ValueError('Expected a complete natural day of HOYs')
    gfa, footprint = float(building.GFA), float(building.FloorArea)
    if min(gfa, footprint, building.StoryHeight) <= 0:
        raise ValueError('Invalid building geometry')
    if not 0 <= building.SkylightArea <= building.RoofArea:
        raise ValueError('Skylight area must be contained in roof area')
    area = {'wall': building._perimeter*building.height*(1-building.wwr), 'roof': building.RoofArea-building.SkylightArea,
            'floor': building.GroundFloorArea, 'window': building._perimeter*building.height*building.wwr,
            'skylight': building.SkylightArea}
    ghi = np.maximum(np.asarray(climate.RadTotal, dtype=float), 0)
    dhi = np.clip(np.asarray(climate.RadDiffuse, dtype=float), 0, ghi)
    if irradiance is None:
        facade = .5*dhi + .25*(ghi-dhi) + .1*ghi
        roof = ghi
        radiation_note = 'Direction-mean facade proxy (no geometric shading); roof GHI.'
    else:
        facade, roof = (np.asarray(irradiance[k], dtype=float) for k in ('facade', 'roof'))
        if facade.shape != (24,) or roof.shape != (24,) or np.any(facade<0) or np.any(roof<0):
            raise ValueError('Invalid daily irradiance')
        radiation_note = 'RAD N/W/S/E equal-direction mean and roof T; not orientation-area weighting.'
    surfaces, solar = [], []
    for name in area:
        if area[name] <= 0:
            continue
        window = name in {'window', 'skylight'}
        u = float(building.WindowUValue if window else building.WallUValue)
        inside = {'roof': .10, 'floor': .17}.get(name, .13)
        cfg = overrides.get(name, {}).copy()
        nominal_u = float(cfg.pop('u_value_w_m2k', u))
        convention = cfg.pop('u_convention', 'air_to_air')
        rsi = float(cfg.pop('reference_inside_m2k_w', inside))
        rse = float(cfg.pop('reference_outside_m2k_w', .04))
        if nominal_u <= 0 or convention not in {'air_to_air', 'surface_to_surface'}:
            raise ValueError('Invalid U or U convention')
        resistance = 1/nominal_u - (rsi+rse if convention == 'air_to_air' else 0)
        if resistance <= 0 and 'resistance_m2k_w' not in cfg:
            raise ValueError('U yields nonpositive construction resistance; supply construction R explicitly')
        tau = float(building.WindowSHGC) if window else 0.0
        surface = dict(name=name, kind=name, area_m2=float(area[name]), resistance_m2k_w=resistance,
            thermal_mass_j_m2k=0.0 if window else (120000.0 if name=='floor' else 60000.0),
            reflectance=(getattr(building,'WindowReflectance',None) if getattr(building,'WindowReflectance',None) is not None else 1-tau) if window else getattr(building,'RoofReflectance' if name=='roof' else 'WallReflectance',.26), transmittance=tau, emissivity=getattr(building,'WindowEmissivity',.84) if window else getattr(building,'RoofEmissivity' if name=='roof' else 'WallEmissivity',.9),
            hi_w_m2k=3.0, ho_w_m2k=0.0 if name=='floor' else 10.0,
            sky_view=0.0 if name=='floor' else (1.0 if name in {'roof','skylight'} else .5),
            ground_view=.5 if name in {'wall','window'} else 0.0,
            ground_contact_w_m2k=100.0 if name=='floor' else 0.0,
            absorbed_inside_fraction=0.0)
        if set(cfg) - (set(surface)|{'layers'}):
            raise ValueError(f'Unknown parameters for {name}: {set(cfg)-set(surface)}')
        if set(cfg) & {'name','kind','area_m2'}:
            raise ValueError('Surface identity and geometry cannot be overridden')
        surface.update(cfg)
        if surface.get('layers'):
            surface['thermal_mass_j_m2k'] = 0.0  # layer capacities are authoritative
        if window and (surface['thermal_mass_j_m2k']!=0 or surface.get('layers')):
            raise ValueError('Window/skylight heat capacity must be zero')
        surfaces.append(surface)
        solar.append(np.zeros(24) if name=='floor' else roof if name in {'roof','skylight'} else facade)
    values = {k: _schedule(building,k,hoy) for k in ('OccupantDensity','FreshAirPerPerson',
        'OccupantHeatGain','EquipmentHeatGain','LightingHeatGain','HeatingSetpointTemp','CoolingSetpointTemp')}
    internal = gfa*(values['OccupantDensity']*values['OccupantHeatGain']+values['EquipmentHeatGain']+values['LightingHeatGain'])
    clock = hoy % 24
    start,end = building.OccupancyStartHour,building.OccupancyEndHour
    occupied = ((clock>=start)&(clock<end)) if start<end else ((clock>=start)|(clock<end))
    ach = np.where(occupied,building.InfiltrationACH,building.NightVentilationACH)
    ach = ach + values['OccupantDensity']*values['FreshAirPerPerson']/building.StoryHeight
    pressure = np.asarray(climate.Pressure, dtype=float)
    hours=[]
    for h in range(24):
        ta=float(climate.Tb[h]);sky=float(climate.SkyTemperature[h])
        if not np.isfinite(sky) or sky<=-273.15 or sky>100:
            raise ValueError('SkyTemperature must be a valid Celsius sky temperature')
        density=pressure[h]/(287.05*(ta+273.15))
        hours.append(dict(air_c=ta,ground_c=float(climate.Tg[h]),sky_ir_w_m2=SIGMA*(sky+273.15)**4,
            irradiance_w_m2=[float(a[h]) for a in solar], internal_w=float(internal[h]),
            heat_setpoint_c=float(values['HeatingSetpointTemp'][h]),cool_setpoint_c=float(values['CoolingSetpointTemp'][h]),
            ventilation_w_k=float(density*1006*gfa*building.StoryHeight*ach[h]/3600),
            ho_w_m2k=[float(overrides.get(s['name'], {}).get('ho_w_m2k', 0.0 if s['kind']=='floor' else 5.8+4.1*float(climate.Velocity[h]))) for s in surfaces]))
    payload=dict(protocol='cetus-surface-7',dt_s=float(options.get('dt_s',3600)),surfaces=surfaces,hours=hours,
        initial_c=float(climate.Tb[0]),cooling_cop=float(building.CoolingEER),heating_cop=float(building.HeatingEER),
        internal_radiant_fraction=float(options.get('internal_radiant_fraction',.6)),
        solar_floor_fraction=float(options.get('solar_floor_fraction',.642)))
    if payload['dt_s']!=3600:
        raise ValueError('Neighborhood climate is hourly; dt_s must be 3600')
    json.dumps(payload, allow_nan=False)
    return payload, {'radiation':radiation_note,'optics':'Archetype SHGC is effective tau with zero absorption unless overridden.',
        'construction':'Roof/floor U default to WallUValue; nominal U reference films removed once.',
        'longwave':'Wall/window sky=.5, ground=.5; roof/skylight sky=1. Ground is a lumped radiative receiver.',
        'thermal_mass':'Default effective capacities are assumptions, not calibrated constructions.'}
