from .engine import (
    build_energy_command,
    ensure_local_energy_binary,
    parse_output_sections,
    run_energy_simulation,
)
from .model import BuildingInstance
from .neighborhood import Neighborhood
from .steady import build_steady_input, run_steady_day

buildEnergyCommand = build_energy_command
ensureLocalEnergyBinary = ensure_local_energy_binary
parseOutputSections = parse_output_sections
runEnergySimulation = run_energy_simulation

__all__ = [
    "build_steady_input",
    "run_steady_day",
    "BuildingInstance",
    "Neighborhood",
    "buildEnergyCommand",
    "build_energy_command",
    "ensureLocalEnergyBinary",
    "ensure_local_energy_binary",
    "parseOutputSections",
    "parse_output_sections",
    "runEnergySimulation",
    "run_energy_simulation",
]

# V8.4 explicit interfaces (legacy aliases above remain unchanged).
from .surface import SurfaceEngine
__version__ = '1.9.0'
__all__ += ['SurfaceEngine', 'BinarySurfaceEngine']

def __getattr__(name):
    if name == "BinarySurfaceEngine":
        from .surface_binary import BinarySurfaceEngine
        return BinarySurfaceEngine
    raise AttributeError(name)

from .standalone import simulate_surface
from .daylight import estimate_daylighting, SpectralWindow, SpectralFractions, DaylightConfig
__all__ += ['simulate_surface','estimate_daylighting','SpectralWindow','SpectralFractions','DaylightConfig']

__version__ = "9.2.0"
