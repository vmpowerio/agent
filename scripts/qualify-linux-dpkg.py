#!/usr/bin/env python3
"""Optional real-package phases called only by the guarded native Linux fixture.

Both exact legacy packages are installed as data/metadata, with their known
postinst and pre-existing synthetic config. Their Node10 executables never run.
Only the built package's actual hooks activate the qualified modern runtime.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import pwd
import stat
import sys

sys.dont_write_bytecode=True
PACKAGE='vmpower-agent'
EXTRA_PATHS=[Path('/usr/lib/vmpower-agent'),Path('/var/lib/vmpower-agent'),Path('/etc/vmpower-template.conf')]
LEGACY={
    'dashboard-linux.deb':{'bytes':10794560,'sha256':'06bf0d767e4625db0477f3e23e30b4e36ac6e7a7d50882b3c655c472f45875b1','binarySha256':'6e26bae20a2d0a45135c0b8591d41982175cff5b844ffcd2c3a610b1b49e3a80'},
    'docs-linux.deb':{'bytes':11090764,'sha256':'e8c0028ea78322e5937becba766654f178e4dfbe1c9817af33e024d93b588311','binarySha256':'fd8bee37cb01a23aff850eb93096a6c8da7aef7ba4e62c548649399420c6849b'},
}

def quarantine():
    spec=importlib.util.spec_from_file_location('package_quarantine',Path(__file__).with_name('linux-package-quarantine.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def verify_legacy(directory):
    if directory.is_symlink() or not directory.is_dir():raise ValueError('Real legacy-debs directory required')
    result={}
    for name,expected in LEGACY.items():
        path=directory/name
        if path.is_symlink() or not path.is_file() or path.stat().st_nlink!=1:raise ValueError('Regular single-link legacy package required')
        raw=path.read_bytes()
        if len(raw)!=expected['bytes'] or hashlib.sha256(raw).hexdigest()!=expected['sha256']:raise ValueError('Exact cached legacy package required: '+name)
        result[name]=path
    return result


def package_state(native):
    result=native.command('dpkg-query','--show','--showformat=${db:Status-Status}|${Version}',PACKAGE,allowed=(0,1))
    return result.stdout.strip() if result.returncode==0 else 'absent'


def require_absent(native):
    if package_state(native)!='absent':raise RuntimeError('Existing package registration is not fixture-owned')


def assert_config(native,raw):
    config=Path('/etc/vmpower.conf');info=config.stat();account=pwd.getpwnam('vmpower')
    assert config.read_bytes()==raw and info.st_uid==0 and info.st_gid==account.pw_gid and stat.S_IMODE(info.st_mode)==0o640
    native.command('runuser','-u','vmpower','--','test','-r',str(config))
    native.command('runuser','-u','nobody','--','test','-r',str(config),allowed=(1,))


def assert_owned(native,expected,version):
    assert package_state(native)=='installed|'+version
    for path in ['/usr/bin/vmpower','/etc/systemd/system/vmpower.service','/usr/lib/vmpower-agent/bundle/runtime/node']:
        assert native.command('dpkg-query','--search',path).stdout.strip()==PACKAGE+': '+path
    assert not native.command('dpkg','--verify',PACKAGE).stdout.strip()
    assert expected in Path('/usr/bin/vmpower').read_text()
    native.command('dpkg-query','--search','/etc/vmpower.conf',allowed=(1,))


def assert_nonroot_child(native,expected):
    executable=Path('/opt/vmpower-agent/releases')/expected/'runtime/node'
    pid=native.child(executable)
    uid_line=next(row for row in Path('/proc/'+str(pid)+'/status').read_text().splitlines() if row.startswith('Uid:'))
    assert {int(n) for n in uid_line.split()[1:]}=={pwd.getpwnam('vmpower').pw_uid}
    assert pwd.getpwnam('vmpower').pw_uid!=0
    return pid


def qualify(native,out,installer,hashes,result):
    native.guard();out=native.require_output(out)
    legacy=verify_legacy(out/'legacy-debs')
    require_absent(native)
    owned=json.loads((out/'owned.json').read_text())
    assert owned.get('dpkgPackage')==PACKAGE and owned['paths']==[str(p) for p in native.MANAGED+EXTRA_PATHS]
    spec=importlib.util.spec_from_file_location('native_deb_builder',native.ROOT/'scripts/build-linux-deb.py')
    builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
    packages=out/'deb-packages';packages.mkdir()
    receipts=result['packages']
    for name,version in [('bundle','0.2.0~rc1'),('upgrade','0.2.0~rc2'),('crashing','0.2.0~rc3')]:
        native.command('dpkg','--compare-versions',version,'gt','0.1')
        receipts[name]=builder.build(out/name,hashes[name],version,packages/(name+'.deb'))
    native.command('dpkg','--compare-versions','0.0.2','gt','0.1',allowed=(1,))
    result['quarantine']=quarantine().begin(native,out,native.state(out)['port'])
    def install(name,allowed=(0,)):
        path=packages/(name+'.deb')
        assert hashlib.sha256(path.read_bytes()).hexdigest()==receipts[name]['sha256']
        return native.command('dpkg','--install',str(path),allowed=allowed)
    def install_legacy(name):
        # Recheck the exact source immediately before every package operation.
        path=verify_legacy(out/'legacy-debs')[name]
        return native.command('dpkg','--install',str(path))
    config=Path('/etc/vmpower.conf');raw=config.read_bytes();unit=Path('/etc/systemd/system/vmpower.service');launcher=Path('/usr/bin/vmpower')
    families=result['families']
    for name in legacy:
        phases=[]
        families.append({'name':name,**LEGACY[name],'legacyBinaryExecuted':False,'legacyActiveRuntimeCompatibility':'UNRUN','phases':phases})
        install_legacy(name);native.command('systemctl','daemon-reload')
        assert package_state(native)=='installed|0.1'
        assert hashlib.sha256(launcher.read_bytes()).hexdigest()==LEGACY[name]['binarySha256']
        assert config.read_bytes()==raw
        native.command('systemctl','is-active','--quiet','vmpower.service',allowed=(3,4))
        # Reproduce the old ownership bug with real dpkg and the standalone installer.
        installer.install(out/'bundle',hashes['bundle'])
        assert native.command('dpkg-query','--search',str(launcher)).stdout.strip()==PACKAGE+': '+str(launcher)
        install_legacy(name)
        assert hashlib.sha256(launcher.read_bytes()).hexdigest()==LEGACY[name]['binarySha256']
        phases.append({'phase':'old-reinstall-replaces-standalone-modern-launcher','status':'REPRODUCED'})
        installer.install(out/'bundle',hashes['bundle'])
        native.command('dpkg','--remove',PACKAGE)
        assert not launcher.exists() and not unit.exists() and config.read_bytes()==raw
        phases.append({'phase':'old-remove-deletes-standalone-modern-entrypoints','status':'REPRODUCED'})
        install_legacy(name);native.command('systemctl','daemon-reload')
        config.chmod(0o644)  # The exact old template/migration baseline was world readable.
        install('bundle')  # Actual candidate preinst/postinst, no direct activation substitute.
        assert_owned(native,hashes['bundle'],receipts['bundle']['version']);assert_config(native,raw)
        native.command('systemctl','is-active','--quiet','vmpower.service',allowed=(3,4))
        native.command('systemctl','is-enabled','--quiet','vmpower.service',allowed=(1,3,4))
        phases.append({'phase':'versioned-upgrade-owns-paths-preserves-stopped-state-and-config','status':'PASS'})
        before=native.state(out)['count'];native.command('systemctl','start','vmpower.service')
        installer.System().stable_child(Path('/opt/vmpower-agent/releases')/hashes['bundle']/'runtime/node')
        pid=assert_nonroot_child(native,hashes['bundle'])
        native.wait(lambda:native.state(out)['count']>=before+2,85)
        assert assert_nonroot_child(native,hashes['bundle'])==pid and native.state(out)['rejected']==0
        phases.append({'phase':'actual-hook-install-nonroot-stable-child-and-two-reports','status':'PASS','reportsObserved':native.state(out)['count']-before})
        install('upgrade');assert_owned(native,hashes['upgrade'],receipts['upgrade']['version']);assert_config(native,raw)
        assert_nonroot_child(native,hashes['upgrade'])
        phases.append({'phase':'actual-hook-active-upgrade','status':'PASS'})
        failed=install('crashing',allowed=(1,))
        assert failed.returncode==1 and package_state(native)=='half-configured|'+receipts['crashing']['version']
        installer.System().stable_child(Path('/opt/vmpower-agent/releases')/hashes['upgrade']/'runtime/node')
        assert_nonroot_child(native,hashes['upgrade']);assert_config(native,raw)
        assert json.loads(Path('/var/lib/vmpower-agent/pending.json').read_text())['state']=='runtime-restored'
        phases.append({'phase':'failed-postinst-restores-runtime-and-exposes-half-configured-dpkg','status':'PASS'})
        install('upgrade')  # Explicit retained-deb rollback reconciles package ownership/version.
        assert_owned(native,hashes['upgrade'],receipts['upgrade']['version']);assert_config(native,raw)
        pid=assert_nonroot_child(native,hashes['upgrade'])
        phases.append({'phase':'retained-prior-deb-restores-installed-registration-and-runtime','status':'PASS'})
        native.command('dpkg','--remove',PACKAGE);native.wait(lambda:native.dead(pid))
        assert not launcher.exists() and not unit.exists() and config.read_bytes()==raw
        assert (Path('/opt/vmpower-agent/releases')/hashes['upgrade']).exists()
        install('upgrade');assert_owned(native,hashes['upgrade'],receipts['upgrade']['version']);assert_config(native,raw)
        pid=assert_nonroot_child(native,hashes['upgrade'])
        native.command('dpkg','--purge',PACKAGE);native.wait(lambda:native.dead(pid));require_absent(native)
        assert not launcher.exists() and not unit.exists() and config.read_bytes()==raw
        phases.append({'phase':'actual-remove-reinstall-purge-preserve-config-and-retained-releases','status':'PASS'})
    result.update({'status':'PASS','packageRegistrationAbsent':True,'actualCandidateHooksExecuted':True,'customerOSCompatibility':'Unknown','retainedConfigSha256':hashlib.sha256(raw).hexdigest()})
    return result
