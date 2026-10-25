"""Local human-operated editor for CETUS Neighborhoods."""

# Set paths before importing any GDAL/PROJ consumer. Desktop installations of
# PostgreSQL/PostGIS and QGIS can export incompatible global PROJ_LIB values.
# These changes are limited to this process and its simulation children.
from .runtime import configure_geospatial_environment, configure_native_environment

configure_native_environment()
configure_geospatial_environment()
