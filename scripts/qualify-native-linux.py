#!/usr/bin/env python3
"""Real systemd lifecycle on an otherwise empty ephemeral Ubuntu runner only."""
import argparse,hashlib,importlib.util,json,os,platform,pwd,grp,shlex,shutil,signal,stat,subprocess,sys,time
from pathlib import Path
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1]
SERVICE_FIXTURE=Path('/opt/vmpower-agent-qualification')
MANAGED=[Path('/opt/vmpower-agent'),Path('/etc/vmpower.conf'),Path('/etc/systemd/system/vmpower.service'),Path('/usr/bin/vmpower'),SERVICE_FIXTURE]
FIXED_ANCESTORS=('/', '/opt', '/etc', '/etc/systemd', '/etc/systemd/system', '/usr', '/usr/bin')
def fixed_ancestor_metadata():
    """Read only an explicit public filesystem metadata allowlist; never repair it."""
    rows=[]
    for name in FIXED_ANCESTORS:
        try:info=Path(name).lstat()
        except FileNotFoundError:
            rows.append({'path':name,'classification':'MISSING'});continue
        classification=('LINKED' if stat.S_ISLNK(info.st_mode) else
                        'NOT_ROOT_OWNED' if info.st_uid!=0 else
                        'GROUP_OR_WORLD_WRITABLE' if info.st_mode & 0o022 else 'PROTECTED')
        rows.append({'path':name,'uid':info.st_uid,'gid':info.st_gid,
                     'mode':format(stat.S_IMODE(info.st_mode),'04o'),'classification':classification})
    return rows
def guard():
    if not (sys.platform=='linux' and os.geteuid()==0 and os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted' and os.environ.get('ImageOS')=='ubuntu24'):
        raise RuntimeError('Root on admitted ephemeral Ubuntu24 runner required')
def command(*args,allowed=(0,),**kwargs):
    result=subprocess.run(args,capture_output=True,text=True,timeout=35,**kwargs)
    if result.returncode not in allowed:raise RuntimeError('Native command failed: '+str(args[0])+' returncode='+str(result.returncode))
    return result
def service_fixture(source,expected,probe,destination):
    """Copy public fixture bytes out of runner-private ancestors; no service action."""
    spec=importlib.util.spec_from_file_location('fixture_installer',ROOT/'scripts/install-linux-bundle.py')
    installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
    # Destination is empty and root owned on the guarded hosted path. Keep it
    # private until copied bytes/modes are verified; never chmod source ancestors.
    destination.mkdir(mode=0o700)
    shutil.copytree(source,destination/'bundle',symlinks=True)
    installer.seal_private_release(destination/'bundle')
    if hashlib.sha256((destination/'bundle/bundle-manifest.json').read_bytes()).hexdigest()!=expected:
        raise ValueError('Service fixture manifest mismatch')
    installer.stage.verify_bundle(destination/'bundle')
    raw=probe.read_bytes();copied=destination/'native-egress-probe.cjs'
    copied.write_bytes(raw);copied.chmod(0o644)
    if hashlib.sha256(copied.read_bytes()).digest()!=hashlib.sha256(raw).digest():raise ValueError('Service probe copy mismatch')
    destination.chmod(0o755)
    return {'manifestSha256':expected,'probeSha256':hashlib.sha256(raw).hexdigest(),'mode':'0755','sourceAncestorsChanged':False}
def egress_probe(node,script,port):
    result=subprocess.run(['runuser','-u','vmpower','--','env','GITHUB_ACTIONS=true','RUNNER_ENVIRONMENT=github-hosted',str(node),str(script),str(port)],capture_output=True,text=True,timeout=15)
    if result.returncode:
        # Only fixed classifications leave this boundary, never raw stderr/env.
        code='UNCLASSIFIED'
        for marker,label in [('Permission denied','EXECUTION_DENIED'),('MODULE_NOT_FOUND','MODULE_NOT_FOUND'),('Owned loopback unavailable','LOOPBACK_UNAVAILABLE'),('Direct nonloopback denial not established','EXTERNAL_DENIAL_UNPROVEN')]:
            if marker in result.stderr:code=label;break
        raise RuntimeError('EGRESS_PROBE_FAILED code='+code+' returncode='+str(result.returncode))
    return result
def write(path,value):path.write_text(json.dumps(value,indent=2)+'\n')
def wait(check,timeout=15):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        value=check()
        if value:return value
        time.sleep(.25)
    raise RuntimeError('Native acceptance condition timed out')
def state(out):
    try:return json.loads((out/'receiver.json').read_text())
    except (FileNotFoundError,json.JSONDecodeError):return {'count':0,'port':None,'rejected':0}
def mainpid():return int(command('systemctl','show','vmpower.service','--property=MainPID','--value').stdout.strip())
def child(expected):
    pid=mainpid()
    if pid<2 or os.readlink('/proc/'+str(pid)+'/exe')!=str(expected):raise RuntimeError('Wrong service child')
    return pid
def dead(pid):return not Path('/proc/'+str(pid)).exists()
def cleanup(out):
    guard();journal=out/'owned.json'
    if out!=(Path(os.environ['RUNNER_TEMP'])/'vmpower-native').resolve():raise RuntimeError('Fixed runner fixture directory required')
    if not journal.exists():return {'status':'NO_OWNED_RESOURCES'}
    owned=json.loads(journal.read_text())
    if owned['run']!=os.environ['GITHUB_RUN_ID'] or owned['paths']!=[str(p) for p in MANAGED]:raise RuntimeError('Cleanup ownership mismatch')
    command('systemctl','stop','vmpower.service',allowed=(0,5))
    command('systemctl','disable','vmpower.service',allowed=(0,1))
    survivors=[]
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():continue
        try:executable=os.readlink(process/'exe')
        except OSError:continue
        if executable.startswith('/opt/vmpower-agent/releases/') or executable==str(SERVICE_FIXTURE/'bundle/runtime/node') or (executable==str(out/'bundle/runtime/node') and int(process.name)!=owned.get('receiverPid')):
            survivors.append(int(process.name));os.kill(int(process.name),signal.SIGKILL)
    if 'receiverPid' in owned:
        pid=owned['receiverPid']
        try:
            argv=Path('/proc/'+str(pid)+'/cmdline').read_bytes()
            if str(out/'receiver.json').encode() in argv:os.kill(pid,signal.SIGTERM)
        except (FileNotFoundError,ProcessLookupError):pass
    for rule in owned.get('firewall',[]):
        binary,args=rule[0],rule[1:]
        if command(binary,'-w','-C','OUTPUT',*args,allowed=(0,1)).returncode==0:command(binary,'-w','-D','OUTPUT',*args)
        assert command(binary,'-w','-C','OUTPUT',*args,allowed=(0,1)).returncode==1
    for path in MANAGED:
        if path.is_dir():shutil.rmtree(path)
        elif path.exists() or path.is_symlink():path.unlink()
    command('systemctl','daemon-reload');command('systemctl','reset-failed','vmpower.service',allowed=(0,1))
    try:pwd.getpwnam('vmpower');command('userdel','vmpower')
    except KeyError:pass
    try:grp.getgrnam('vmpower');command('groupdel','vmpower')
    except KeyError:pass
    if 'receiverPid' in owned:
        # A child owned by this process can be a zombie until waited; reap it here.
        try:os.waitpid(owned['receiverPid'],0)
        except ChildProcessError:wait(lambda:dead(owned['receiverPid']))
    assert all(not p.exists() for p in MANAGED)
    assert command('systemctl','show','vmpower.service','--property=LoadState','--value').stdout.strip()=='not-found'
    for path in out.iterdir():
        if path.name in ['evidence','owned.json']:continue
        if path.is_dir():shutil.rmtree(path)
        else:path.unlink()
    result={'status':'PASS','serviceAbsent':True,'childrenAbsent':not survivors,'managedFilesAbsent':True,'fixtureInputsRemoved':True,'firewallRulesRemoved':True,'receiverStopped':True}
    write(out/'evidence/cleanup-linux.json',result)
    if survivors:raise RuntimeError('Service left children; forced cleanup performed')
    return result
def qualify(out):
    guard()
    if out!=(Path(os.environ['RUNNER_TEMP'])/'vmpower-native').resolve():raise RuntimeError('Fixed runner fixture directory required')
    # Do not read any pre-existing configuration: reject the entire occupied host.
    if any(p.exists() or p.is_symlink() for p in MANAGED):raise RuntimeError('Managed destination already exists')
    if command('systemctl','show','vmpower.service','--property=LoadState','--value').stdout.strip()!='not-found':raise RuntimeError('Service already exists')
    for lookup in [pwd.getpwnam,grp.getgrnam]:
        try:lookup('vmpower')
        except KeyError:continue
        raise RuntimeError('Service account/group already exists')
    owned={'run':os.environ['GITHUB_RUN_ID'],'paths':[str(p) for p in MANAGED],'firewall':[]};write(out/'owned.json',owned)
    prepared=json.loads((out/'evidence/prepared.json').read_text());hashes=prepared['bundles'];node=SERVICE_FIXTURE/'bundle/runtime/node'
    phases=[];result={'status':'FAIL','head':prepared['head'],'platform':platform.platform(),'image':os.environ.get('ImageOS'),'imageVersion':os.environ.get('ImageVersion'),'architecture':platform.machine(),'libc':platform.libc_ver(),'phases':phases,'customerOSCompatibility':'Unknown'}
    try:
        spec=importlib.util.spec_from_file_location('installer',ROOT/'scripts/install-linux-bundle.py');installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
        result['fixedAncestorMetadata']=fixed_ancestor_metadata()
        installer.System().destination(SERVICE_FIXTURE)
        result['serviceFixture']=service_fixture(out/'bundle',hashes['bundle'],ROOT/'scripts/native-egress-probe.cjs',SERVICE_FIXTURE)
        command('useradd','--system','--user-group','--no-create-home','--home-dir','/nonexistent','--shell','/usr/sbin/nologin','vmpower')
        uid=pwd.getpwnam('vmpower').pw_uid
        for binary,address in [('iptables','127.0.0.1/32'),('ip6tables','::1/128')]:
            args=['-m','owner','--uid-owner',str(uid),'!','-d',address,'-m','comment','--comment','VMPOWER_NATIVE_FIXTURE','-j','REJECT']
            owned['firewall'].append([binary,*args]);write(out/'owned.json',owned)
            command(binary,'-w','-I','OUTPUT','1',*args)
        receiver=subprocess.Popen([str(out/'bundle/runtime/node'),str(ROOT/'scripts/native-report-fixture.cjs'),str(out/'receiver.json')],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        owned['receiverPid']=receiver.pid;write(out/'owned.json',owned)
        port=wait(lambda:state(out)['port'])
        proof=egress_probe(node,SERVICE_FIXTURE/'native-egress-probe.cjs',port)
        counters=command('iptables','-w','-L','OUTPUT','-n','-v','-x').stdout.splitlines()
        assert any('VMPOWER_NATIVE_FIXTURE' in row and int(row.split()[0])>0 for row in counters)
        phases.append({'phase':'outbound-denial','status':'PASS','probe':json.loads(proof.stdout),'ipv4RulePacketsPositive':True,'ipv6RuleInstalled':True})
        no_config=command('runuser','-u','vmpower','--',str(node),str(SERVICE_FIXTURE/'bundle/app/index.js'),allowed=(1,))
        assert 'required option' in no_config.stderr
        phases.append({'phase':'real-cli-no-config','status':'PASS'})
        config={'key':'fixture "key"','secret':'fixture\\secret$(literal)','vm_id':'fixture-native','host':'localhost:'+str(port)}
        command(sys.executable,str(ROOT/'scripts/install-linux-bundle.py'),'install','--apply','--bundle',str(out/'bundle'),'--expected-manifest-sha256',hashes['bundle'],'--config-stdin',input=json.dumps(config))
        config_digest=hashlib.sha256(MANAGED[1].read_bytes()).hexdigest()
        runtime=MANAGED[0]/'releases'/hashes['bundle']/'runtime/node';pid=child(runtime)
        wait(lambda:state(out)['count']>=2,85)
        assert child(runtime)==pid and state(out)['rejected']==0
        interval=state(out)['lastReportAt']-state(out)['firstReportAt'];assert interval>=59000
        phases.append({'phase':'fresh-install-stable-child-periodic-report','status':'PASS','reports':state(out)['count'],'observedIntervalMs':interval,'configuredPeriodSeconds':60})
        assert MANAGED[1].stat().st_mode & 0o777==0o640
        command('systemctl','stop','vmpower.service');wait(lambda:dead(pid));count=state(out)['count']
        command('systemctl','start','vmpower.service');wait(lambda:state(out)['count']>count);pid=child(runtime)
        phases.append({'phase':'stop-start','status':'PASS'})
        count=state(out)['count'];installer.install(out/'upgrade',hashes['upgrade'])
        wait(lambda:state(out)['count']>count);assert dead(pid)
        runtime=MANAGED[0]/'releases'/hashes['upgrade']/'runtime/node';child(runtime)
        assert hashlib.sha256(MANAGED[1].read_bytes()).hexdigest()==config_digest
        phases.append({'phase':'upgrade-preserves-config','status':'PASS'})
        count=state(out)['count'];failed=False
        try:installer.install(out/'crashing',hashes['crashing'])
        except (RuntimeError,OSError,subprocess.SubprocessError):failed=True
        assert failed,'Crashing child was incorrectly accepted'
        wait(lambda:state(out)['count']>count);child(runtime)
        assert hashes['upgrade'] in MANAGED[3].read_text()
        assert hashlib.sha256(MANAGED[1].read_bytes()).hexdigest()==config_digest
        phases.append({'phase':'real-crashing-child-rollback','status':'PASS'})
        command('systemctl','stop','vmpower.service');command('systemctl','disable','vmpower.service')
        installer.install(out/'bundle',hashes['bundle'])
        assert command('systemctl','is-active','--quiet','vmpower.service',allowed=(0,3)).returncode==3
        assert command('systemctl','is-enabled','--quiet','vmpower.service',allowed=(0,1)).returncode==1
        phases.append({'phase':'stopped-disabled-upgrade','status':'PASS'})
        installer.uninstall()
        assert hashlib.sha256(MANAGED[1].read_bytes()).hexdigest()==config_digest and (MANAGED[0]/'releases'/hashes['upgrade']).exists()
        phases.append({'phase':'uninstall-preserves-config-and-releases','status':'PASS'})
        # Synthetic legacy unit/launcher contract, using qualified Node24 bytes.
        # This does not claim compatibility with an old published Node10 binary.
        MANAGED[2].write_bytes(installer.LEGACY_UNIT)
        MANAGED[3].write_text('#!/bin/sh\nexec '+shlex.quote(str(node))+' '+shlex.quote(str(SERVICE_FIXTURE/'bundle/app/index.js'))+' "$@"\n')
        MANAGED[3].chmod(0o755);command('systemctl','daemon-reload')
        count=state(out)['count'];command('systemctl','start','vmpower.service');wait(lambda:state(out)['count']>count);legacy_pid=child(node)
        count=state(out)['count'];migration=installer.install(out/'upgrade',hashes['upgrade'])
        wait(lambda:state(out)['count']>count);child(runtime);assert dead(legacy_pid)
        assert (Path(migration['rollbackFiles'])/'vmpower.service').read_bytes()==installer.LEGACY_UNIT
        assert hashlib.sha256(MANAGED[1].read_bytes()).hexdigest()==config_digest
        installer.uninstall()
        phases.append({'phase':'synthetic-legacy-unit-migration','status':'PASS','legacyPublishedBinary':'UNRUN'})
        result['status']='PASS'
    finally:
        try:result['cleanup']=cleanup(out)
        except Exception:result['status']='FAIL';result['cleanup']={'status':'FAIL'};raise
        finally:write(out/'evidence/lifecycle-linux.json',result)
    print(json.dumps({'nativeLifecycle':'PASS','phases':len(phases),'cleanup':result['cleanup']['status']}))
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--cleanup',action='store_true');args=parser.parse_args();guard()
    if args.cleanup:cleanup(args.output.resolve())
    else:qualify(args.output.resolve())
