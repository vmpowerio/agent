"""Bounded fixture commands. Raw output is memory-only; failures use closed labels."""
import json, os, selectors, signal, subprocess, tempfile, time
from pathlib import Path
LIMIT=65536
EVIDENCE=None
OPERATION_FILE='operation-linux.json'
class Interrupted(RuntimeError):
    def __init__(self,number):self.number=number;super().__init__('FIXTURE_SIGNAL')
def handle_signals():
    def received(number,frame):raise Interrupted(number)
    for number in (signal.SIGTERM,signal.SIGINT):signal.signal(number,received)

class CommandFailure(RuntimeError):
    def __init__(self,record):
        self.record=record
        super().__init__('NATIVE_COMMAND_'+record['classification'])

def atomic(path,value):
    fd,name=tempfile.mkstemp(prefix='.native-json-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            os.fchmod(stream.fileno(),0o644)
            json.dump(value,stream,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)

def category(args):
    name=Path(args[0]).name
    if name=='dpkg':
        for flag,label in [('--install','DPKG_INSTALL'),('--remove','DPKG_REMOVE'),('--purge','DPKG_PURGE')]:
            if flag in args:return label
        return 'DPKG_QUERY'
    return {'systemctl':'SYSTEMCTL','systemd-run':'OWNED_SCOPE','dpkg-query':'DPKG_QUERY',
            'runuser':'SERVICE_USER','python3':'PYTHON','node':'NODE'}.get(name,'FIXED_HELPER')

def labels(raw):
    text=raw.decode('utf-8','replace').lower()
    return [label for label,markers in {
        'DPKG_LOCK_HELD':('could not get lock','frontend lock was locked','database lock was locked','unable to lock'),
        'DPKG_INTERRUPTED':('dpkg was interrupted',),
        'MAINTAINER_SCRIPT_FAILED':('subprocess returned error exit status','script subprocess returned error'),
        'PERMISSION_DENIED':('permission denied',),
        'ISOLATION_REFUSED':('fixture_isolation_',),
    }.items() if any(marker in text for marker in markers)] or ['UNCLASSIFIED']

def run(*args,allowed=(0,),timeout=35,input=None,env=None,cwd=None,on_spawn=None):
    args=tuple(str(a) for a in args)
    record={'operation':category(args),'state':'STARTED','startedMonotonic':time.monotonic()}
    if EVIDENCE:atomic(EVIDENCE/OPERATION_FILE,record)
    buffers={'stdout':bytearray(),'stderr':bytearray()};proc=None;problem=None
    try:
        raw_input=input.encode() if isinstance(input,str) else input
        if raw_input is not None and len(raw_input)>LIMIT:raise ValueError('FIXTURE_INPUT_LIMIT')
        with tempfile.TemporaryFile() as stdin:
            if raw_input:stdin.write(raw_input)
            stdin.seek(0)
            proc=subprocess.Popen(args,stdin=stdin,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                  env=env,cwd=cwd,start_new_session=True)
            if on_spawn:on_spawn(proc.pid)
            with selectors.DefaultSelector() as selected:
                for key in buffers:
                    stream=getattr(proc,key);os.set_blocking(stream.fileno(),False)
                    selected.register(stream,selectors.EVENT_READ,key)
                while selected.get_map():
                    if time.monotonic()-record['startedMonotonic']>timeout:problem='TIMEOUT';break
                    for event,_ in selected.select(.05):
                        chunk=os.read(event.fileobj.fileno(),4096)
                        if not chunk:selected.unregister(event.fileobj);continue
                        buffers[event.data].extend(chunk)
                        if sum(map(len,buffers.values()))>LIMIT:problem='OUTPUT_LIMIT';break
                    if problem:break
                if not problem:
                    try:proc.wait(timeout=max(.01,timeout-(time.monotonic()-record['startedMonotonic'])))
                    except subprocess.TimeoutExpired:problem='TIMEOUT'
    except BaseException as exc:
        problem=problem or 'INTERRUPTED'
        if isinstance(exc,Interrupted):record['signal']=exc.number
        raise
    finally:
        if proc is not None:
            if problem:
                try:os.killpg(proc.pid,signal.SIGKILL)
                except ProcessLookupError:pass
            try:proc.wait(timeout=5)
            except subprocess.TimeoutExpired:problem='REAP_TIMEOUT'
            for key in buffers:
                stream=getattr(proc,key)
                if stream:stream.close()
        record.update(state='FINISHED',elapsedSeconds=round(time.monotonic()-record['startedMonotonic'],3),
                      exitCode=proc.returncode if proc else None,
                      classification=problem or ('SIGNAL' if proc.returncode<0 else 'SUCCESS' if proc.returncode in allowed else 'EXIT_NONZERO'))
        record['diagnosticLabels']=labels(bytes(buffers['stderr'])) if record['classification']!='SUCCESS' else []
        if EVIDENCE:atomic(EVIDENCE/OPERATION_FILE,record)
    if problem or proc.returncode not in allowed:raise CommandFailure(record)
    return subprocess.CompletedProcess(args,proc.returncode,bytes(buffers['stdout']).decode('utf-8','replace'),bytes(buffers['stderr']).decode('utf-8','replace'))
