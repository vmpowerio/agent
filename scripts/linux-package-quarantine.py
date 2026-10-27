#!/usr/bin/env python3
"""Fixture slice supervision. Never change host firewall or runner networking."""
import argparse,ctypes,errno,inspect,json,os,platform,re,socket,sys,threading,uuid
from contextlib import contextmanager
from pathlib import Path
import native_linux_commands as commands
from native_fixture_paths import require_output
ROOT=Path(__file__).resolve().parents[1]
SYSTEM=Path('/run/systemd/system')
DROP=SYSTEM/'vmpower.service.d'
PROBE=Path('/opt/vmpower-agent-qualification/slice-probe.py')

class AdmissionRefused(RuntimeError):
    def __init__(self,reason,resource='local'):
        self.reason=reason;self.resource=resource
        super().__init__('FIXTURE_ISOLATION_UNIT_COLLISION' if reason in ('LOADED_UNIT','UNIT_FILE','DROPIN','CGROUP') else 'FIXTURE_ISOLATION_ADMISSION_REFUSED')

def failure_record(exc,phase):
    reason=(exc.reason if isinstance(exc,AdmissionRefused) else
            'QUERY_FAILED' if isinstance(exc,commands.CommandFailure) else
            'SIGNAL' if isinstance(exc,commands.Interrupted) else
            'IO_FAILED' if isinstance(exc,OSError) else 'UNKNOWN')
    return {'phase':phase,'resource':exc.resource if isinstance(exc,AdmissionRefused) else 'local','reason':reason}

def occupied(target):
    try:Path(target).lstat();return True
    except FileNotFoundError:return False

def guard():
    if not (sys.platform=='linux' and os.geteuid()==0 and platform.machine()=='x86_64' and os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted' and os.environ.get('ImageOS')=='ubuntu24'):
        raise RuntimeError('FIXTURE_ISOLATION_HOST_REFUSED')

def names():
    run=os.environ.get('GITHUB_RUN_ID','')
    if not re.fullmatch('[0-9]+',run):raise ValueError('FIXTURE_ISOLATION_RUN_REFUSED')
    return {'run':run,'slice':'vmpowerfixture'+run+'.slice','qualify':'vmpowerqualify'+run+'.scope','cleanup':'vmpowercleanup'+run+'.scope'}

def member(pid,unit=None):
    expected='/'+names()['slice']+'/'
    rows=Path('/proc/'+str(pid)+'/cgroup').read_text().splitlines()
    if len(rows)!=1 or not rows[0].startswith('0::'+expected):raise RuntimeError('FIXTURE_ISOLATION_MEMBERSHIP_REFUSED')
    group=rows[0][3:]
    if unit and group!=expected+unit:raise RuntimeError('FIXTURE_ISOLATION_UNIT_REFUSED')
    return group

def effective_filters(group):
    """Read-only x86_64 Linux BPF_PROG_QUERY; unsupported fails before sockets.

    Stable linux/bpf.h query ABI, effective ancestor programs, no BPF load/write.
    This avoids installing a new privileged utility on the hosted runner.
    """
    guard()
    if not group.startswith('/'+names()['slice']+'/') or '..' in group:raise RuntimeError('FIXTURE_ISOLATION_CGROUP_REFUSED')
    class Query(ctypes.Structure):
        _fields_=[('target_fd',ctypes.c_uint32),('attach_type',ctypes.c_uint32),('query_flags',ctypes.c_uint32),('attach_flags',ctypes.c_uint32),('prog_ids',ctypes.c_uint64),('prog_cnt',ctypes.c_uint32)]
    counts=[];libc=ctypes.CDLL(None,use_errno=True)
    fd=os.open('/sys/fs/cgroup'+group,os.O_RDONLY|os.O_DIRECTORY)
    try:
        for attach in (0,1): # BPF_CGROUP_INET_INGRESS / EGRESS
            ids=(ctypes.c_uint32*64)();q=Query(fd,attach,1,0,ctypes.addressof(ids),64)
            if libc.syscall(321,16,ctypes.byref(q),ctypes.sizeof(q))!=0 or not 0<q.prog_cnt<=64:raise RuntimeError('FIXTURE_ISOLATION_BPF_UNSUPPORTED')
            counts.append(q.prog_cnt)
    finally:os.close(fd)
    return {'ingressPrograms':counts[0],'egressPrograms':counts[1]}

def probe(run,port):
    # Copied verbatim for actual service ExecStartPre and dpkg pre-invoke hooks.
    # No private checkout access, environment output or application imports.
    if not re.fullmatch('[0-9]+',run):raise RuntimeError('FIXTURE_ISOLATION_RUN_REFUSED')
    expected='/vmpowerfixture'+run+'.slice/'
    rows=Path('/proc/self/cgroup').read_text().splitlines()
    if len(rows)!=1 or not rows[0].startswith('0::'+expected):raise RuntimeError('FIXTURE_ISOLATION_MEMBERSHIP_REFUSED')
    with socket.create_connection(('127.0.0.1',int(port)),timeout=3):pass
    outcomes={}
    for family,address,label in [(socket.AF_INET,'1.1.1.1','ipv4'),(socket.AF_INET6,'2606:4700:4700::1111','ipv6')]:
        with socket.socket(family,socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            try:connection.connect((address,443))
            except OSError as exc:
                # These results only count alongside independently queried
                # effective kernel filters and exact slice-policy readback.
                if isinstance(exc,TimeoutError):outcomes[label]='FILTERED_TIMEOUT'
                elif exc.errno in (errno.EACCES,errno.EPERM,errno.ENETUNREACH,errno.EHOSTUNREACH,errno.ECONNREFUSED):outcomes[label]=errno.errorcode[exc.errno]
                else:raise RuntimeError('FIXTURE_ISOLATION_DENIAL_UNCLASSIFIED') from None
            else:raise RuntimeError('FIXTURE_ISOLATION_EXTERNAL_CONNECTED')
    return {'membership':'PASS','loopback':'PASS',**outcomes}

def probe_bytes():
    return ('import errno,json,re,socket,sys\nfrom pathlib import Path\n'+inspect.getsource(probe)+
            '\ntry: print(json.dumps(probe(sys.argv[1],int(sys.argv[2]))))\nexcept Exception: print("FIXTURE_ISOLATION_PROBE_REFUSED",file=sys.stderr);sys.exit(1)\n').encode()

def description(data,key):return 'VMPower fixture '+data['ownershipToken']+' '+key

def policy(port=0,token='UNADMITTED'):
    n=names()
    return {SYSTEM/n['slice']:('[Unit]\nDescription=VMPower fixture '+token+' slice\n[Slice]\nIPAddressDeny=any\nIPAddressAllow=127.0.0.1/32 ::1/128\n').encode(),
            DROP/'90-vmpower-fixture.conf':('[Service]\nSlice='+n['slice']+'\nExecStartPre=/usr/bin/python3 '+str(PROBE)+' '+n['run']+' '+str(port)+'\n').encode()}

def journal(out):
    data=json.loads((out/'isolation.json').read_text())
    if (data['identity']!=names() or data['state'] not in ('INTENT','READY') or
        data.get('unitsAbsentAtAdmission') is not True or not re.fullmatch('[0-9a-f]{32}',data.get('ownershipToken','')) or
        set(data.get('unitStates',{}))!={'slice','qualify','cleanup'}):raise RuntimeError('FIXTURE_ISOLATION_JOURNAL_REFUSED')
    return data

def verify(out):
    guard();out=require_output(out);data=journal(out)
    for target,raw in policy(data.get('port',0),data['ownershipToken']).items():
        if target.is_symlink() or target.read_bytes()!=raw:raise RuntimeError('FIXTURE_ISOLATION_POLICY_DRIFT')
    # Check the loaded policy as well as exact runtime files.
    deny=commands.run('systemctl','show',names()['slice'],'--property=IPAddressDeny','--value').stdout.strip()
    allow=commands.run('systemctl','show',names()['slice'],'--property=IPAddressAllow','--value').stdout.strip()
    if set(deny.split())!={'0.0.0.0/0','::/0'} or set(allow.split())!={'127.0.0.1/32','::1/128'}:raise RuntimeError('FIXTURE_ISOLATION_LOADED_POLICY_REFUSED')
    return effective_filters(member(os.getpid()))

def prepare_service(out,port):
    verify(out);data=journal(out)
    if not isinstance(port,int) or not 0<port<65536:raise ValueError('FIXTURE_ISOLATION_PORT_REFUSED')
    if data.get('port') not in (None,port):raise RuntimeError('FIXTURE_ISOLATION_PORT_DRIFT')
    data['port']=port;commands.atomic(out/'isolation.json',data)
    target=DROP/'90-vmpower-fixture.conf';target.write_bytes(policy(port,data['ownershipToken'])[target]);target.chmod(0o644)
    PROBE.write_bytes(probe_bytes());PROBE.chmod(0o644)
    commands.run('systemctl','daemon-reload');verify(out)

def begin(native,out,port):
    filters=verify(out);root=probe(names()['run'],port)
    descendant=commands.run(sys.executable,str(PROBE),names()['run'],str(port),on_spawn=lambda pid:member(pid))
    return {'status':'PASS','scope':'owned qualifier/package-hook/service cgroups only','kernelFilters':filters,
            'root':root,'rootDescendant':json.loads(descendant.stdout),'serviceExecStartPre':'required on every start',
            'hostFirewallChanged':False,'runnerControlOutsideSlice':True}

def package_args(out,args):
    if Path(args[0]).name=='dpkg' and any(a in args for a in ('--install','--remove','--purge')):
        verify(out);data=journal(out)
        if not data.get('port') or PROBE.read_bytes()!=probe_bytes():raise RuntimeError('FIXTURE_ISOLATION_HOOK_GUARD_REFUSED')
        hook='/usr/bin/python3 '+str(PROBE)+' '+names()['run']+' '+str(data['port'])
        return (args[0],'--pre-invoke='+hook,*args[1:])
    return args

@contextmanager
def cleanup_receiver(out):
    # The old receiver died with the previous scope. Cleanup pre-invoke hooks
    # still require a positive fixture-only loopback control, not the application.
    data=journal(out);port=data.get('port')
    if not port:yield;return
    stopped=threading.Event()
    with socket.socket() as server:
        server.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        server.bind(('127.0.0.1',port));server.listen();server.settimeout(.1)
        def receive():
            while not stopped.is_set():
                try:connection,_=server.accept();connection.close()
                except socket.timeout:continue
        worker=threading.Thread(target=receive,daemon=True);worker.start()
        try:yield
        finally:
            stopped.set();worker.join(1)
            if worker.is_alive():raise RuntimeError('FIXTURE_ISOLATION_RECEIVER_REMAINS')

def unit_group(key):
    n=names()
    return Path('/sys/fs/cgroup')/n['slice'] if key=='slice' else Path('/sys/fs/cgroup')/n['slice']/n[key]

def admit_units():
    # All three units and fixed cgroup paths must be absent BEFORE intent,
    # file creation, systemd-run or stop. Named property queries load an absent
    # implicit slice, so enumerate existing objects without querying those names.
    n=names();keys=('slice','qualify','cleanup');units=[n[key] for key in keys]
    for verb,reason in (('list-units','LOADED_UNIT'),('list-unit-files','UNIT_FILE')):
        args=['systemctl',verb,'--plain','--full','--no-legend','--no-pager']
        if verb=='list-units':args.append('--all')
        rows=commands.run(*args,*units).stdout.splitlines()
        for row in rows:
            if not row.strip():continue
            unit=row.split()[0]
            if unit not in units:raise AdmissionRefused('PARSE_FAILED')
            raise AdmissionRefused(reason,keys[units.index(unit)])
    # The manager's lookup paths include control, transient and generator units.
    # Drop-in-only definitions and dangling masks may be absent from both lists.
    raw=commands.run('systemctl','show','--property=UnitPath','--value').stdout.strip()
    paths=raw.split()
    if not paths or len(paths)>64:raise AdmissionRefused('PARSE_FAILED')
    for directory in paths:
        if not re.fullmatch(r'/[A-Za-z0-9_./-]+',directory) or '..' in Path(directory).parts:
            raise AdmissionRefused('PARSE_FAILED')
        for key in keys:
            target=Path(directory)/n[key]
            if occupied(target):raise AdmissionRefused('UNIT_FILE',key)
            if occupied(str(target)+'.d'):raise AdmissionRefused('DROPIN',key)
    for key in ('slice','qualify','cleanup'):
        if occupied(unit_group(key)):raise AdmissionRefused('CGROUP',key)

def create_policy(target,raw):
    # Never overwrite a file that appeared after the admission snapshot.
    with target.open('xb') as stream:
        os.fchmod(stream.fileno(),0o644);stream.write(raw)

def stop_owned(out,key):
    data=journal(out);unit=names()[key];group=unit_group(key)
    shown=commands.run('systemctl','show',unit,'--property=LoadState','--property=Description','--property=ControlGroup').stdout
    props=dict(line.split('=',1) for line in shown.splitlines() if '=' in line)
    if props.get('LoadState')=='not-found':
        if group.exists():raise RuntimeError('FIXTURE_ISOLATION_UNOWNED_CGROUP')
        final_state='ABSENT'
    else:
        expected='/'+str(group.relative_to('/sys/fs/cgroup'))
        if (data['unitStates'][key] not in ('CREATE_INTENT','OWNED','STOPPED') or
            props.get('Description')!=description(data,key) or props.get('ControlGroup') not in ('',expected)):
            raise RuntimeError('FIXTURE_ISOLATION_UNIT_OWNERSHIP_REFUSED')
        # Creation is confirmed by the unique journaled description and exact
        # cgroup, not inferred from a failed systemd-run attempt/name alone.
        data['unitStates'][key]='OWNED';commands.atomic(out/'isolation.json',data)
        if key=='slice' and group.exists() and any(p.read_text().strip() for p in group.rglob('cgroup.procs')):
            raise RuntimeError('FIXTURE_ISOLATION_DESCENDANTS_REMAIN')
        commands.run('systemctl','stop',unit,allowed=(0,5),timeout=15)
        if group.exists() and any(p.read_text().strip() for p in group.rglob('cgroup.procs')):raise RuntimeError('FIXTURE_ISOLATION_DESCENDANTS_REMAIN')
        final_state='STOPPED' # Do not call a still-loaded inactive unit absent.
    data['unitStates'][key]=final_state;commands.atomic(out/'isolation.json',data)

def stop_scope(unit,out):
    keys=[key for key in ('qualify','cleanup') if names()[key]==unit]
    if not keys:raise RuntimeError('FIXTURE_ISOLATION_SCOPE_REFUSED')
    stop_owned(out,keys[0])

def require_empty(unit):
    if unit not in (names()['qualify'],names()['cleanup'],'vmpower.service'):raise RuntimeError('FIXTURE_ISOLATION_SCOPE_REFUSED')
    group=Path('/sys/fs/cgroup')/names()['slice']/unit
    if group.exists() and any(p.read_text().strip() for p in group.rglob('cgroup.procs')):raise RuntimeError('FIXTURE_ISOLATION_DESCENDANTS_REMAIN')

def supervise(out,cleanup_only=False,legacy=False):
    guard();out=require_output(out);n=names();record=out/'isolation.json';commands.EVIDENCE=out/'evidence'
    commands.OPERATION_FILE='operation-supervisor-linux.json'
    # The runner/control supervisor must never enter the fixture slice itself.
    if any(row.startswith('0::/'+n['slice']) for row in Path('/proc/self/cgroup').read_text().splitlines()):raise RuntimeError('FIXTURE_ISOLATION_SUPERVISOR_INSIDE')
    if cleanup_only and not record.exists():raise AdmissionRefused('NO_JOURNAL')
    if record.exists():
        saved=json.loads(record.read_text())
        if saved.get('identity')!=n:raise RuntimeError('FIXTURE_ISOLATION_JOURNAL_REFUSED')
        if saved.get('state')=='REMOVED':return {'status':'ALREADY_REMOVED'}
    result={'status':'FAIL','qualifierScopeEmpty':False,'cleanupScopeEmpty':False,'isolationRemoved':False};error=None;phase='LOCAL_PREFLIGHT'
    supervisor=out/'evidence/supervisor-outcome-linux.json'
    if cleanup_only and supervisor.exists():result['primarySupervisorOutcome']=json.loads(supervisor.read_text())
    outcome=out/'evidence/qualifier-outcome-linux.json'
    if cleanup_only and outcome.exists():result['qualifierOutcome']=json.loads(outcome.read_text())
    def scope(mode):
        data=journal(out);data['unitStates'][mode]='CREATE_INTENT';commands.atomic(record,data)
        args=['systemd-run','--scope','--quiet','--unit='+n[mode],'--slice='+n['slice'],
              '--property=Description='+description(data,mode),'--property=TimeoutStopSec=5s','--',sys.executable,str(ROOT/'scripts/qualify-native-linux.py'),
              '--inside-slice','--output',str(out)]
        if mode=='cleanup':args.append('--cleanup')
        elif legacy:args.append('--legacy-dpkg')
        return commands.run(*args,timeout=750)
    try:
        if not cleanup_only:
            if occupied(record) or occupied(DROP) or any(occupied(p) for p in policy()):raise AdmissionRefused('LOCAL_PATH')
            phase='SERVICE_PREFLIGHT'
            if commands.run('systemctl','show','vmpower.service','--property=LoadState','--value').stdout.strip()!='not-found':raise AdmissionRefused('LOADED_UNIT','service')
            phase='UNIT_ADMISSION'
            admit_units()
            data={'identity':n,'state':'INTENT','unitsAbsentAtAdmission':True,'ownershipToken':uuid.uuid4().hex,
                  'unitStates':{'slice':'CREATE_INTENT','qualify':'NOT_CREATED','cleanup':'NOT_CREATED'}}
            commands.atomic(record,data)
            phase='POLICY_CREATION'
            DROP.mkdir(mode=0o755)
            for target,raw in policy(token=data['ownershipToken']).items():create_policy(target,raw)
            commands.run('systemctl','daemon-reload')
            data['state']='READY';commands.atomic(record,data)
            commands.atomic(outcome,{'status':'RUNNING'})
            phase='QUALIFIER'
            try:
                scope('qualify');result['qualifierOutcome']={'status':'PASS'}
            except BaseException as exc:
                error=exc
                failure=exc.record if isinstance(exc,commands.CommandFailure) else {'classification':'SIGNAL','signal':exc.number} if isinstance(exc,commands.Interrupted) else {'classification':'QUALIFIER_REFUSED'}
                result['qualifierOutcome']={'status':'FAIL','failure':failure}
            # Persist before cleanup can replace the latest operation receipt.
            commands.atomic(outcome,result['qualifierOutcome'])
        # Previous cancellation can leave root dpkg/hooks. Stop and reap the
        # entire owned scope before any fresh cleanup hooks, never kill by name.
        phase='REAP_QUALIFIER';stop_scope(n['qualify'],out);result['qualifierScopeEmpty']=True
        phase='CLEANUP'
        if (out/'owned.json').exists():
            commands.atomic(out/'evidence/cleanup-linux.json',{'status':'RUNNING','resourceAbsence':'Unknown'})
            try:scope('cleanup')
            except BaseException as exc:
                # Keep the inner, more specific DPKG/lock/hook failure if it
                # finalized. Otherwise persist the outer interruption category.
                receipt=out/'evidence/cleanup-linux.json'
                if json.loads(receipt.read_text()).get('status')!='FAIL':
                    commands.atomic(receipt,{'status':'FAIL','resourceAbsence':'Unknown',
                        'failure':exc.record if isinstance(exc,commands.CommandFailure) else {'classification':'CLEANUP_SCOPE_INTERRUPTED_OR_REFUSED'}})
                raise
            finally:stop_scope(n['cleanup'],out)
        else:
            # Qualifier journals intent before any app/account/package mutation.
            # Unsupported BPF can therefore safely remove only its owned policy.
            if commands.run('systemctl','show','vmpower.service','--property=LoadState','--value').stdout.strip()!='not-found':raise RuntimeError('FIXTURE_ISOLATION_UNJOURNALED_SERVICE')
            commands.atomic(out/'evidence/cleanup-linux.json',{'status':'PASS','serviceAbsent':True,'childrenAbsent':True,'noOwnedResources':True})
        result['cleanupScopeEmpty']=True
        cleaned=json.loads((out/'evidence/cleanup-linux.json').read_text())
        if cleaned.get('status')!='PASS':raise RuntimeError('FIXTURE_ISOLATION_CLEANUP_INCOMPLETE')
        lifecycle=out/'evidence/lifecycle-linux.json'
        if lifecycle.exists():
            accepted=json.loads(lifecycle.read_text());accepted['cleanup']=cleaned
            commands.atomic(lifecycle,accepted)
        data=journal(out)
        phase='REMOVE_POLICY'
        stop_owned(out,'slice')
        for target,raw in policy(data.get('port',0),data['ownershipToken']).items():
            if not target.exists() and data['state']=='INTENT':continue
            if target.is_symlink() or target.read_bytes()!=raw:raise RuntimeError('FIXTURE_ISOLATION_POLICY_DRIFT')
            target.unlink()
        if DROP.exists():DROP.rmdir()
        commands.run('systemctl','daemon-reload')
        data=journal(out);data['state']='REMOVED'
        result.update(status='PASS',isolationRemoved=True);commands.atomic(record,data)
        if not cleanup_only:commands.atomic(supervisor,{'status':'FAIL' if error else 'PASS','phase':'QUALIFIER' if error else 'COMPLETE',**({'failure':failure_record(error,'QUALIFIER')} if error else {})})
    except BaseException as exc:
        result['failure']=failure_record(exc,phase)
        # A later always-cleanup invocation cannot replace the original refusal.
        if not cleanup_only or not supervisor.exists():commands.atomic(supervisor,{'status':'FAIL','failure':result['failure']})
        raise
    finally:
        commands.atomic(out/'evidence/isolation-linux.json',result)
        receipt=out/'evidence/cleanup-linux.json'
        # Includes setup/preflight interruption and failure to reap before purge.
        # SIGKILL/runner loss cannot execute finally; the always step retries.
        if result['status']!='PASS' and not receipt.exists():
            commands.atomic(receipt,{'status':'FAIL','resourceAbsence':'Unknown','failure':{'classification':'CLEANUP_NOT_COMPLETED'}})
    if error:raise error
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--cleanup',action='store_true');parser.add_argument('--legacy-dpkg',action='store_true');args=parser.parse_args()
    try:
        commands.handle_signals()
        supervise(args.output,args.cleanup,args.legacy_dpkg)
    except BaseException as exc:
        print(json.dumps({'status':'FAIL','classification':exc.record['classification'] if isinstance(exc,commands.CommandFailure) else 'ISOLATION_OR_INTERRUPTION_REFUSED'}));sys.exit(1)
