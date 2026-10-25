"""Startup branches without modifying user environments during unit tests."""
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from view import bootstrap


def test_isolates_gis_and_conda_dll_search_path(tmp_path):
    import os
    import sys
    broken=tmp_path/'foreign-proj';broken.mkdir();(broken/'proj.db').write_bytes(b'not a PROJ database')
    env={**os.environ,'PROJ_LIB':str(broken),'PROJ_DATA':str(broken),'GDAL_DATA':str(broken),
         'PYTHONDONTWRITEBYTECODE':'1'}
    command=[sys.executable,'-c',
        'import view; import numpy as np; from rasterio.crs import CRS; '
        'assert str(CRS.from_epsg(32650)) == "EPSG:32650"; '
        'assert np.linalg.solve(np.eye(2), [1,2]).tolist() == [1,2]']
    result=subprocess.run(command,env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_conda_named_environment(monkeypatch,tmp_path):
    prefix=tmp_path/'CETUS';prefix.mkdir();(prefix/'python.exe').touch()
    monkeypatch.setenv('CONDA_EXE','conda.exe')
    monkeypatch.setattr(bootstrap.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout=json.dumps({'envs':[str(prefix)]}),stderr=''))
    assert bootstrap.existing_conda_python()==prefix/'python.exe'


def test_plain_python_creates_venv_and_installs(monkeypatch,tmp_path):
    (tmp_path/'view').mkdir();(tmp_path/'requirements.txt').write_text('numpy\n')
    (tmp_path/'view'/'requirements.txt').write_text('Flask\n')
    monkeypatch.setattr(bootstrap,'ROOT',tmp_path)
    monkeypatch.setattr(bootstrap,'existing_conda_python',lambda:None)
    calls=[]
    def run(command,**kwargs):
        calls.append(command)
        if command[1:3]==['-m','venv']:
            folder=Path(command[-1]);(folder/'Scripts').mkdir(parents=True);(folder/'Scripts'/'python.exe').touch()
        return SimpleNamespace(returncode=1 if kwargs.get('capture_output') else 0)
    monkeypatch.setattr(bootstrap.subprocess,'run',run)
    bootstrap.launch()
    assert any(c[1:3]==['-m','venv'] for c in calls)
    assert any(c[1:4]==['-m','pip','install'] for c in calls)
    assert calls[-1][1:]==['-m','view','--open']
    assert calls[-1][0]==str(tmp_path/'.venv'/'CETUS'/'Scripts'/'python.exe')


def test_reuse_conda_without_venv(monkeypatch,tmp_path):
    (tmp_path/'view').mkdir();(tmp_path/'requirements.txt').write_text('numpy\n')
    (tmp_path/'view'/'requirements.txt').write_text('Flask\n')
    env=tmp_path/'envs'/'CETUS';env.mkdir(parents=True);python=env/'python.exe';python.touch()
    monkeypatch.setattr(bootstrap,'ROOT',tmp_path)
    monkeypatch.setattr(bootstrap,'existing_conda_python',lambda:python)
    calls=[]
    def run(command,**kwargs):
        calls.append(command);return SimpleNamespace(returncode=0)
    monkeypatch.setattr(bootstrap.subprocess,'run',run)
    bootstrap.launch()
    assert all(c[1:3]!=['-m','venv'] for c in calls)
    assert calls[-1][0]==str(python)
