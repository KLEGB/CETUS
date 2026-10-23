"""Native CES3 client; no JSON encode/decode during begin_step/evaluate.

One client is sequential, matching the legacy SurfaceEngine. Each begin_step
copies the physical old state into the engine; evaluate never commits it.
"""
from __future__ import annotations
import copy
import os
import queue
import struct
import subprocess
import tempfile
import threading
from .surface import ensure_surface_binary
from .surface_codec import HEADER,MAGIC,MAX_FRAME,FIELDS,frame,encode_models,encode_step,encode_evaluate,decode_evaluate

class BinarySurfaceEngine:
    def __init__(self,executable_path=None,timeout=120,*,warm_start=True,candidate_cache=True):
        self.timeout=timeout;self.errors=tempfile.TemporaryFile(mode='w+t');self.models=[];self.step_id=0;self.request_id=0;self.active=False;self.last_candidate_id=None;self.warm_start=warm_start;self.candidate_cache=candidate_cache
        self.process=subprocess.Popen([str(ensure_surface_binary(executable_path)),'--binary'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.errors,bufsize=0,env={**os.environ,'GOMAXPROCS':os.environ.get('CETUS_ENERGY_WORKERS','4')})
        self.responses=queue.Queue();self.reader=threading.Thread(target=self._read,daemon=True);self.reader.start()
    def _exact(self,n):
        chunks=[]
        while n:
            b=self.process.stdout.read(n)
            if not b:raise EOFError('Engine closed the response stream')
            chunks.append(b);n-=len(b)
        return b''.join(chunks)
    def _read(self):
        try:
            while True:
                h=self._exact(HEADER.size);magic,v,op,rid,n=HEADER.unpack(h)
                if magic!=MAGIC or v!=1 or n>MAX_FRAME:raise ValueError('Invalid binary response header')
                self.responses.put((op,rid,self._exact(n)))
        except Exception as e:self.responses.put(e)
    def _send(self,op,payload):
        self.request_id+=1;rid=self.request_id
        try:
            data=memoryview(frame(op,payload,rid))
            while data:
                n=self.process.stdin.write(data)
                if not n:raise BrokenPipeError('Short write')
                data=data[n:]
            answer=self.responses.get(timeout=self.timeout)
            if isinstance(answer,Exception):
                self.close();raise answer
            code,reply_id,raw=answer
            if reply_id!=rid:raise RuntimeError('Response ID differs')
            if code==65535:raise ValueError(raw.decode('utf-8'))
            if code!=op:raise RuntimeError('Response operation differs')
            return raw
        except (BrokenPipeError,EOFError,queue.Empty) as exc:
            self.close();raise RuntimeError('Binary engine stopped or timed out') from exc
    def configure(self,models):
        answer=self._send(1,encode_models(models))
        self.models=copy.deepcopy(models);self.active=False;self.last_candidate_id=None
        self._send(8,struct.pack('<II',int(self.warm_start),int(self.candidate_cache)))
        return {'configured':struct.unpack('<I',answer)[0]}
    def begin_step(self,batch):
        self.step_id+=1
        self._send(2,encode_step(batch,self.step_id));self.active=True;self.last_candidate_id=None
        return self.step_id
    def evaluate(self,hours,*,response_level='totals',return_state=False,aggregation='batch',dynamic_fields=FIELDS):
        if not self.active:raise ValueError('begin_step is required')
        if len(hours)!=len(self.models):raise ValueError('Model count differs')
        raw=self._send(7,encode_evaluate(hours,self.step_id,response_level,return_state,aggregation,dynamic_fields))
        self.last_candidate_id=struct.unpack_from('<Q',raw)[0]
        return decode_evaluate(raw[8:],self.models,response_level,return_state,aggregation)
    def get_result(self,candidate_id,*,response_level='full',return_state=True,aggregation='batch'):
        from .surface_codec import LEVELS
        if not self.active:raise ValueError('No active step')
        if aggregation not in ('batch','building'):raise ValueError('Unknown aggregation')
        raw=self._send(6,struct.pack('<QQIII',self.step_id,candidate_id,LEVELS[response_level],int(return_state),int(aggregation=='building')))
        return decode_evaluate(raw,self.models,response_level,return_state,aggregation)
    def profile(self):
        raw=self._send(9,b'');values=struct.unpack('<8Q4d',raw)
        names=('evaluate_requests','get_result_requests','building_solves','building_cache_hits','diagnostic_rebuilds','nonlinear_iterations','warm_starts','cold_fallbacks','decode_validate_seconds','parallel_solve_seconds','result_format_seconds','frame_write_seconds')
        return dict(zip(names,values))
    def end_step(self):
        if self.active:self._send(4,struct.pack('<Q',self.step_id));self.active=False;self.last_candidate_id=None
    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
        for s in (self.process.stdin,self.process.stdout,self.errors):
            if not s.closed:s.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
