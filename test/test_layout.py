"""Package boundaries and entry points after repository consolidation."""
import json
import os
from pathlib import Path
import subprocess
import sys

from ENERGY.result_store import ResultWriter
from test.paths import PROJECT_ROOT, DATA, VALIDATION, ENERGY_CONFIGS


def test_root_and_resources():
    assert not any((PROJECT_ROOT/name).exists() for name in (
        'Energy.py','EnergyUnpack.py','result_store.py','test.py','tests','scripts','examples','configs','validation'))
    assert (DATA/'input_2026_05_27.csv').is_file()
    assert (VALIDATION/'coupled_input/53695_3092_LCZ1_2B.zip').is_file()
    assert (ENERGY_CONFIGS/'selective_one_bounce.json').is_file()
    # Go tests have one canonical location under test/.
    assert not list((PROJECT_ROOT/'ENERGY').rglob('*_test.go'))
    assert not list((PROJECT_ROOT/'RAD').rglob('*_test.go'))


def test_package_import_and_help_without_site_packages():
    code=('import sys, ENERGY; from ENERGY.result_store import read_results; '
          'assert "numpy" not in sys.modules and "scipy" not in sys.modules; '
          'assert "Neighborhood" in ENERGY.__all__')
    subprocess.run([sys.executable,'-S','-c',code],cwd=PROJECT_ROOT,check=True,capture_output=True)
    for module in ('ENERGY','ENERGY.unpack'):
        subprocess.run([sys.executable,'-S','-m',module,'--help'],cwd=PROJECT_ROOT,check=True,capture_output=True)


def test_unpack_from_unrelated_working_directory(tmp_path):
    archive=tmp_path/'source.bin'
    with ResultWriter(archive,[{'building_id':'one'}]) as writer:
        writer.record(0,rows=[{'hvac_w':-123.}]);writer.finish()
    env={**os.environ,'PYTHONPATH':str(PROJECT_ROOT)}
    for command,name in [(['-m','ENERGY.unpack'],'module.json'),
                         ([str(PROJECT_ROOT/'ENERGY'/'unpack.py')],'script.json')]:
        output=tmp_path/name
        subprocess.run([sys.executable,'-S',*command,str(archive),str(output)],
                       cwd=tmp_path,env=env,check=True,capture_output=True)
        assert json.loads(output.read_text())['buildings'][0]['hours'][0]['hvac_w']==-123.


def test_energy_cli_from_unrelated_working_directory(tmp_path):
    from test.test_coupled import small
    n=small();source=tmp_path/'input.json';n.saveJSON(source)
    target=tmp_path/'output.bin'
    subprocess.run([sys.executable,str(PROJECT_ROOT/'ENERGY'/'cli.py'),
                    '--input','input.json','--output','output.bin'],
                   cwd=tmp_path,check=True,capture_output=True)
    assert target.is_file()
    assert (tmp_path/'output_neighborhood.json').is_file()
