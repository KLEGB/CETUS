"""Compatibility bridge for RAD weather helpers sourced from WEA.epw."""

from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from WEA.epw import (
    EpwLocation,
    build_hourly_sky_matrix,
    day_hour_indices,
    ensure_local_weather_binaries,
    epw2location,
    load_epw_radiation,
    load_sun_vectors,
)

buildHourlySkyMatrix = build_hourly_sky_matrix
dayHourIndices = day_hour_indices
ensureLocalWeatherBinaries = ensure_local_weather_binaries
epw2Location = epw2location
loadEpwRadiation = load_epw_radiation
loadSunVectors = load_sun_vectors
