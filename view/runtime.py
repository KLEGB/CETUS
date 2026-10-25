"""Use the active Python environment's GIS databases, never another app's."""
import importlib.util
import os
from pathlib import Path
import sys

_dll_handles=[]


def configure_native_environment():
    """Absolute conda python.exe needs its DLL paths even without activation."""
    if os.name!='nt':return
    prefix=Path(sys.prefix)
    folders=[prefix,prefix/'Library'/'mingw-w64'/'bin',prefix/'Library'/'usr'/'bin',
             prefix/'Library'/'bin',prefix/'Scripts',prefix/'bin']
    folders=[p for p in folders if p.is_dir()]
    existing=os.environ.get('PATH','').split(os.pathsep)
    leading={str(p).lower() for p in folders}
    os.environ['PATH']=os.pathsep.join([str(p) for p in folders]+[p for p in existing if p.lower() not in leading])
    if hasattr(os,'add_dll_directory'):
        for p in folders:_dll_handles.append(os.add_dll_directory(str(p)))


def configure_geospatial_environment():
    spec=importlib.util.find_spec('rasterio')
    package=Path(spec.origin).parent if spec and spec.origin else None
    prefixes=[Path(sys.prefix)/'Library'/'share',Path(sys.prefix)/'share']
    proj_candidates=([package/'proj_data'] if package else [])+[p/'proj' for p in prefixes]
    pyproj=importlib.util.find_spec('pyproj')
    if pyproj and pyproj.origin:
        proj_candidates.append(Path(pyproj.origin).parent/'proj_dir'/'share'/'proj')
    proj=next((p for p in proj_candidates if (p/'proj.db').is_file()),None)
    gdal_candidates=([package/'gdal_data'] if package else [])+[p/'gdal' for p in prefixes]
    gdal=next((p for p in gdal_candidates if p.is_dir()),None)
    if proj:
        os.environ['PROJ_DATA']=str(proj)
        os.environ['PROJ_LIB']=str(proj)
    if gdal:os.environ['GDAL_DATA']=str(gdal)
    return dict(proj=str(proj) if proj else None,gdal=str(gdal) if gdal else None)
