"""CETUS energy interfaces; lazy exports keep unpacking standard-library-only."""
from importlib import import_module

__version__ = '9.2.0'
_EXPORTS = {
    'BuildingInstance': ('model', 'BuildingInstance'),
    'Neighborhood': ('neighborhood', 'Neighborhood'),
    'build_energy_command': ('engine', 'build_energy_command'),
    'buildEnergyCommand': ('engine', 'build_energy_command'),
    'ensure_local_energy_binary': ('engine', 'ensure_local_energy_binary'),
    'ensureLocalEnergyBinary': ('engine', 'ensure_local_energy_binary'),
    'parse_output_sections': ('engine', 'parse_output_sections'),
    'parseOutputSections': ('engine', 'parse_output_sections'),
    'run_energy_simulation': ('engine', 'run_energy_simulation'),
    'runEnergySimulation': ('engine', 'run_energy_simulation'),
    'build_steady_input': ('steady', 'build_steady_input'),
    'run_steady_day': ('steady', 'run_steady_day'),
    'SurfaceEngine': ('surface', 'SurfaceEngine'),
    'BinarySurfaceEngine': ('surface_binary', 'BinarySurfaceEngine'),
    'simulate_surface': ('standalone', 'simulate_surface'),
    'estimate_daylighting': ('daylight', 'estimate_daylighting'),
    'SpectralWindow': ('daylight', 'SpectralWindow'),
    'SpectralFractions': ('daylight', 'SpectralFractions'),
    'DaylightConfig': ('daylight', 'DaylightConfig'),
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
    module, attribute = _EXPORTS[name]
    value = getattr(import_module(f'.{module}', __name__), attribute)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
