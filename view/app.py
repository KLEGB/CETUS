"""Loopback-only Flask editor; no frontend build tools or external tile service."""
from __future__ import annotations

import argparse
import atexit
import copy
import json
import os
import random
import socket
import subprocess
import sys
import threading
import uuid
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException

from ENERGY import Neighborhood
from .model import Editor, ROOT, number
from .simulation import settings


def create_app(data_dir=None):
    app=Flask(__name__)
    app.config['JSON_AS_ASCII']=False
    app.json.ensure_ascii=False
    editor=Editor(data_dir)
    lock=threading.RLock()
    jobs={}
    app.extensions['editor']=editor
    app.extensions['jobs']=jobs

    def refresh(job):
        if job['process'].poll() is None:return
        status_path=job['folder']/'status.json'
        if job['status']=='running':
            result=json.loads(status_path.read_text(encoding='utf-8')) if status_path.is_file() else dict(status='failed',error='计算进程意外退出，请查看日志')
            job.update(result)
            if job['status']=='complete':
                # Commit completed climate and references once, retaining the map.
                editor.n=Neighborhood.loadJSON(job['folder']/'completed_neighborhood.json')

    def mutable():
        for job in jobs.values():
            refresh(job)
            if job['status']=='running':raise ValueError('计算正在运行，请先停止计算再编辑方案')

    @app.before_request
    def local_only():
        # Block cross-site requests to this local file-editing service.
        if request.host.split(':')[0] not in ('127.0.0.1','localhost','[::1]'):
            return jsonify(error='仅允许本机访问'),403
        origin=request.headers.get('Origin')
        if origin and origin!=request.host_url.rstrip('/'):
            return jsonify(error='不允许跨站请求'),403

    @app.errorhandler(Exception)
    def error(exc):
        if isinstance(exc,HTTPException):return jsonify(error=exc.description),exc.code
        if isinstance(exc,(ValueError,KeyError,FileNotFoundError,PermissionError,TypeError)):
            return jsonify(error=str(exc)),400
        app.logger.exception('Editor operation failed')
        return jsonify(error=str(exc)),500

    @app.get('/')
    def index():return render_template('index.html')

    @app.get('/api/state')
    def state():
        with lock:
            for job in jobs.values():refresh(job)
            payload=editor.snapshot()
            payload['active_job']=next((key for key,job in jobs.items() if job['status']=='running'),None)
            return jsonify(payload)

    @app.post('/api/import/shp')
    def import_shp():
        with lock:
            mutable();p=request.get_json()
            result=editor.import_shp(p['path'],p.get('height_field'))
            return jsonify(result)

    @app.post('/api/import/weather')
    def import_weather():
        with lock:
            mutable();p=request.get_json()
            zone=p.get('climate_zone')
            lat=lon=None
            if p.get('latitude') is not None:
                lat=number(p['latitude'],'纬度');lon=number(p['longitude'],'经度')
                if not -90<=lat<=90 or not -180<=lon<=180:raise ValueError('经纬度超出范围')
            editor.import_weather(p['path'],p['start_date'],p['end_date'],int(zone) if zone else None)
            if lat is not None and not editor.n._epw:
                from dataclasses import replace
                editor.n.location=replace(editor.n.location,latitude=lat,longitude=lon)
            return jsonify(ok=True)

    @app.post('/api/import/rad')
    def import_rad():
        with lock:
            mutable();p=request.get_json();path=p.get('path','').strip()
            if path:
                candidate=copy.deepcopy(editor.n)
                candidate.radSimulation(radFile=Path(path).resolve())
                net=candidate._radiation_network
                if not all(int(h) in net.lookup for c in candidate.climate for h in c.HOY):
                    raise ValueError('RAD 文件不覆盖当前计算时段')
                editor.n=candidate
                if 'sample' in net.meta:editor.state['sample']=float(net.meta['sample'])
            else:editor.invalidate()
            return jsonify(ok=True)

    @app.post('/api/select')
    def select():
        with lock:
            mutable();p=request.get_json();ids=[b.building_id for b in editor.n.buildings]
            if p.get('random'):
                ratio=number(p.get('ratio',.3),'选择比例')
                if not 0<=ratio<=1:raise ValueError('随机选择比例必须在 0–1 之间')
                seed=int(p.get('seed',42));editor.state.update(random_ratio=ratio,seed=seed)
                count=int(np_ceil(len(ids)*ratio))
                selected=random.Random(seed).sample(ids,count)
            else:selected=p.get('ids',[])
            if not isinstance(selected,list) or set(selected)-set(ids):raise ValueError('选择集无效')
            editor.state['selected']=list(dict.fromkeys(selected))
            return jsonify(selected=editor.state['selected'])

    @app.post('/api/buildings')
    def buildings():
        with lock:
            mutable();p=request.get_json()
            editor.apply_buildings(editor.state['selected'],p.get('values',{}),p.get('uncertainty',{}),p.get('kind'))
            return jsonify(ok=True)

    @app.post('/api/urban')
    def urban():
        with lock:
            mutable();p=request.get_json()
            if p.get('restore'):editor.restore_urban()
            else:editor.apply_urban(p.get('data',{}),p.get('lcz','LCZx'))
            return jsonify(ok=True)

    @app.post('/api/save')
    def save():
        with lock:
            mutable();path=editor.save(request.get_json()['path'])
            return jsonify(path=str(path))

    @app.post('/api/options')
    def options():
        with lock:
            mutable();p=request.get_json();candidate=copy.deepcopy(editor.state)
            for key in ('iteration','sample','load_tolerance','seed','random_ratio'):
                if key in p:candidate[key]=p[key]
            settings(candidate)
            seed=number(candidate['seed'],'随机种子')
            if seed!=int(seed):raise ValueError('随机种子必须是整数')
            ratio=number(candidate['random_ratio'],'随机选择比例')
            if not 0<=ratio<=1:raise ValueError('随机选择比例必须在 0–1 之间')
            candidate['seed']=int(seed);candidate['random_ratio']=ratio
            if float(candidate['sample'])!=float(editor.state['sample']):editor.invalidate()
            editor.n.view_state=candidate
            return jsonify(ok=True)

    @app.post('/api/load')
    def load():
        with lock:
            mutable();editor.load(request.get_json()['path'])
            return jsonify(ok=True)

    @app.post('/api/dialog')
    def dialog():
        # Tk must own the main thread of its process, not a Flask request thread.
        kind=request.get_json().get('kind','open')
        completed=subprocess.run([sys.executable,'-m','view.dialog',kind],cwd=ROOT,capture_output=True,
                                 text=True,encoding='utf-8',env={**os.environ,'PYTHONIOENCODING':'utf-8'},
                                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if completed.returncode:raise RuntimeError('无法打开文件选择器，请手动填写路径。'+completed.stderr[-500:])
        return jsonify(json.loads(completed.stdout))

    @app.post('/api/run')
    def run():
        with lock:
            mutable();p=request.get_json()
            candidate=copy.deepcopy(editor.state)
            for k in ('iteration','sample','load_tolerance','seed'):
                if k in p:candidate[k]=p[k]
            settings(candidate)
            if not editor.n.buildings or not candidate.get('weather_loaded'):raise ValueError('请先导入 SHP 和气象文件')
            editor.n.view_state=candidate
            editor.remember_urban()
            job_id=uuid.uuid4().hex
            folder=editor.data_dir/'runs'/job_id;folder.mkdir(parents=True)
            editor.n.saveJSON(folder/'input.json')
            logfile=(folder/'console.log').open('w',encoding='utf-8')
            try:
                proc=subprocess.Popen([sys.executable,'-u','-m','view.worker',str(folder)],cwd=ROOT,
                                      stdout=logfile,stderr=subprocess.STDOUT,
                                      env={**os.environ,'PYTHONIOENCODING':'utf-8'},
                                      creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            finally:logfile.close()
            jobs[job_id]=dict(process=proc,folder=folder,status='running')
            return jsonify(id=job_id)

    @app.get('/api/jobs/<job_id>')
    def job_status(job_id):
        with lock:
            job=jobs[job_id];refresh(job)
            logpath=job['folder']/'console.log'
            offset=max(0,int(request.args.get('offset',0)))
            with logpath.open('rb') as f:
                f.seek(offset);chunk=f.read(256*1024);offset=f.tell()
            result=dict(status=job['status'],error=job.get('error'),logs=chunk.decode('utf-8',errors='replace'),offset=offset)
            progress=job['folder']/'progress.json'
            if progress.is_file():result['progress']=json.loads(progress.read_text(encoding='utf-8'))
            if job['status']=='complete':result['result']=json.loads((job['folder']/'results.json').read_text(encoding='utf-8'))
            return jsonify(result)

    @app.post('/api/jobs/<job_id>/stop')
    def stop(job_id):
        with lock:
            job=jobs[job_id]
            if job['process'].poll() is None:
                terminate(job['process']);job['status']='cancelled'
            return jsonify(status=job['status'])

    @app.get('/api/jobs/<job_id>/download/<name>')
    def download(job_id,name):
        allowed={'results.json','N_hourly.csv','F_hourly.csv','N_building_results.json','F_building_results.json'}
        if name not in allowed:raise ValueError('未知结果文件')
        return send_file(jobs[job_id]['folder']/name,as_attachment=True)

    def cleanup():
        for job in jobs.values():
            if job['process'].poll() is None:terminate(job['process'])
    atexit.register(cleanup)
    return app


def np_ceil(value):
    import math
    return math.ceil(value)


def terminate(proc):
    if os.name=='nt':
        subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                       creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    else:proc.terminate()
    try:proc.wait(timeout=10)
    except subprocess.TimeoutExpired:proc.kill();proc.wait()


def main():
    parser=argparse.ArgumentParser(description='CETUS 本地可视化编辑器')
    parser.add_argument('--port',type=int,default=5000)
    parser.add_argument('--open',action='store_true')
    args=parser.parse_args()
    from werkzeug.serving import make_server
    app=create_app()
    # Binding before opening the browser avoids the first-load race. Pick an
    # available port if 5000 is occupied by another local application.
    server=None
    for port in range(args.port,args.port+20):
        with socket.socket() as probe:
            try:probe.bind(('127.0.0.1',port))
            except OSError:continue
        server=make_server('127.0.0.1',port,app,threaded=True);break
    if server is None:raise RuntimeError('没有可用的本机端口')
    url=f'http://127.0.0.1:{server.server_port}'
    print(f'CETUS View: {url}\n关闭此控制台或按 Ctrl+C 停止服务。',flush=True)
    if args.open:webbrowser.open(url)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=='__main__':main()
