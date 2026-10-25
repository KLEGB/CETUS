"""Standard-library-only Windows environment discovery and startup."""
from __future__ import annotations
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parent.parent


def popup(message):
    if os.name=='nt':ctypes.windll.user32.MessageBoxW(0,message,'CETUS 启动失败',0x10)
    print(message,file=sys.stderr)


def existing_conda_python():
    conda=os.environ.get('CONDA_EXE') or shutil.which('conda.exe')
    if not conda:
        batch=shutil.which('conda.bat')
        if batch:
            for parent in Path(batch).parents:
                candidate=parent/'Scripts'/'conda.exe'
                if candidate.is_file():conda=str(candidate);break
    if not conda:
        # conda.bat may be the only command exported by Anaconda Prompt.
        base=Path(sys.executable).parent
        for path in (base/'Scripts'/'conda.exe',base.parent.parent/'Scripts'/'conda.exe'):
            if path.is_file():conda=str(path);break
    if not conda:return None
    result=subprocess.run([conda,'env','list','--json'],capture_output=True,text=True,encoding='utf-8',errors='replace')
    if result.returncode:raise RuntimeError('无法读取 Conda 环境列表：'+result.stderr[-1500:])
    for environment in json.loads(result.stdout).get('envs',[]):
        path=Path(environment)
        if path.name.lower()=='cetus' and (path/'python.exe').is_file():return path/'python.exe'
    return None


def launch():
    if sys.version_info<(3,12):raise RuntimeError('CETUS 需要 Python 3.12 或更高版本，请先安装。')
    python=existing_conda_python()
    if python is None:
        environment=ROOT/'.venv'/'CETUS'
        python=environment/'Scripts'/'python.exe'
        if not python.is_file():
            print('正在创建项目虚拟环境 .venv\\CETUS…',flush=True)
            subprocess.run([sys.executable,'-m','venv',str(environment)],check=True)
    subprocess.run([str(python),'-c','import sys; assert sys.version_info >= (3,12), "CETUS requires Python 3.12+"'],check=True)
    print('CETUS Python: '+str(python),flush=True)
    fingerprint=hashlib.sha256((ROOT/'requirements.txt').read_bytes()+(ROOT/'view'/'requirements.txt').read_bytes()).hexdigest()
    environment_root=python.parent.parent if python.parent.name.lower()=='scripts' else python.parent
    stamp=environment_root/'.cetus_dependencies.json'
    check="import importlib.util; assert all(importlib.util.find_spec(x) for x in ('flask','numpy','scipy','geopandas','shapely','rasterio','pandas','pyproj','psutil','optuna','matplotlib'))"
    installed=subprocess.run([str(python),'-c',check],capture_output=True).returncode==0
    previous=json.loads(stamp.read_text()) if stamp.is_file() else {}
    if not installed or previous.get('requirements')!=fingerprint:
        print('正在安装/检查 CETUS 依赖，首次启动可能需要数分钟…',flush=True)
        subprocess.run([str(python),'-m','pip','install','-r',str(ROOT/'view'/'requirements.txt')],cwd=ROOT,check=True)
        subprocess.run([str(python),'-c',check],check=True)
        stamp.write_text(json.dumps(dict(requirements=fingerprint)),encoding='utf-8')
    result=subprocess.run([str(python),'-m','view','--open'],cwd=ROOT)
    if result.returncode:raise RuntimeError(f'Flask 服务异常退出（代码 {result.returncode}），请查看控制台。')


if __name__=='__main__':
    try:launch()
    except KeyboardInterrupt:pass
    except Exception as exc:
        popup('启动失败：'+str(exc)+'\n请查看控制台错误信息，修复后重新运行 StartView.bat。')
        raise SystemExit(1)
