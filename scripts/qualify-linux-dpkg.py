#!/usr/bin/env python3
"""Optional real-package phases called only by the guarded native Linux fixture.

Both exact legacy packages are installed as data/metadata, with their known
postinst and pre-existing synthetic config. Their Node10 executables never run.
Only the built package's actual hooks activate the qualified modern runtime.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import stat
import sys

sys.dont_write_bytecode=True
PACKAGE='vmpower-agent'
FINAL_NAME='vmpower-agent_0.2.0_amd64.deb'
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


def final_identity():
    source=os.environ.get('QUALIFIED_SOURCE_SHA','');tree=os.environ.get('QUALIFIED_SOURCE_TREE','')
    if not re.fullmatch('[a-f0-9]{40}',source) or not re.fullmatch('[a-f0-9]{40}',tree):raise ValueError('Exact final source identity required')
    if os.environ.get('GITHUB_REPOSITORY')!='vmpowerio/agent' or os.environ.get('GITHUB_JOB')!='linux-native':raise ValueError('Final package job route refused')
    return {'repository':'vmpowerio/agent','source':source,'tree':tree,'runId':quarantine().canonical_run_id(os.environ.get('GITHUB_RUN_ID','')),
            'attempt':quarantine().canonical_run_id(os.environ.get('GITHUB_RUN_ATTEMPT','')),'job':'linux-native'}


def final_bundle(builder,out,expected,root):
    manifest=builder.installer.stage.verify_bundle(out/'bundle')
    if (manifest.get('qualification')!='Staged bytes only; native execution, installer/service and release acceptance are separate.' or
            manifest['applicationVersion']!=json.loads((root/'package.json').read_text())['version'] or
            manifest['sourceLockSha256']!=hashlib.sha256((root/'package-lock.json').read_bytes()).hexdigest() or
            manifest['files']['app/index.js']!=hashlib.sha256((root/'build/index.js').read_bytes()).hexdigest() or
            hashlib.sha256((out/'bundle/bundle-manifest.json').read_bytes()).hexdigest()!=expected):
        raise ValueError('Only the unmodified compiled bundle may be retained')
    pins=json.loads((root/'distribution/runtime-pins.json').read_text())
    pin=pins['targets']['linux-x64']
    runtime=builder.installer.stage.runtime_files(out/pin['archive'],'linux-x64',pins)
    if manifest['runtimeArchiveSha256']!=pin['sha256'] or manifest['nodeVersion']!=pins['nodeVersion']:raise ValueError('Original runtime archive binding failed')
    if any((out/'bundle/runtime'/name).read_bytes()!=raw for name,raw in runtime.items()):raise ValueError('Runtime/license differs from original pinned archive')
    return manifest


def verify_retained(out):
    """Success-only upload gate; same retained bytes, no rebuild or config export."""
    from native_fixture_paths import require_output
    out=require_output(out);directory=out/'evidence/final-linux'
    if directory.is_symlink() or {p.name for p in directory.iterdir()}!={FINAL_NAME,'manifest.json'}:raise ValueError('Unexpected retained inventory')
    info=(directory/'manifest.json').lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or not 0<info.st_size<=65536:raise ValueError('Bounded regular final manifest required')
    manifest=json.loads((directory/'manifest.json').read_text())
    if set(manifest)!={'repository','source','tree','runId','attempt','job','status','package','applicationVersion','nodeVersion','runtimeArchiveSha256','sourceLockSha256','legacyFamilies','postSuccessRollback','customerOSCompatibility','licensesIncluded'}:raise ValueError('Final manifest schema refused')
    if set(manifest['package'])!={'package','version','architecture','sha256','bytes','bundleManifestSha256','maintainerHookSha256','files'}:raise ValueError('Final package schema refused')
    if any(manifest['package'][k]!=v for k,v in {'package':'vmpower-agent','version':'0.2.0','architecture':'amd64'}.items()):raise ValueError('Final package identity refused')
    if manifest['legacyFamilies']!=sorted(LEGACY) or manifest['customerOSCompatibility']!='Unknown':raise ValueError('Final cohort evidence refused')
    if any(manifest[k]!=v for k,v in final_identity().items()):raise ValueError('Retained source/job identity mismatch')
    evidence=out/'evidence';lifecycle=json.loads((evidence/'lifecycle-linux.json').read_text())
    cleanup=json.loads((evidence/'cleanup-linux.json').read_text());supervisor=json.loads((evidence/'supervisor-outcome-linux.json').read_text())
    isolation=json.loads((out/'isolation.json').read_text())
    if lifecycle['status']!='PASS' or lifecycle['head']!=manifest['source'] or supervisor['status']!='PASS' or isolation['state']!='REMOVED':raise ValueError('Final qualification not complete')
    if cleanup['status']!='PASS' or not all(cleanup[k] is True for k in ('serviceAbsent','childrenAbsent','managedFilesAbsent','packageRegistrationAbsent')):raise ValueError('Final cleanup not proved')
    qualified=lifecycle['realPackageOwnership']
    if qualified['status']!='PASS' or qualified['finalPackage']!=manifest:raise ValueError('Final lifecycle binding mismatch')
    if qualified['packages']['final']!=manifest['package'] or sorted(f['name'] for f in qualified['families'])!=sorted(LEGACY):raise ValueError('Final family/package evidence mismatch')
    for family in qualified['families']:
        phases=[p for p in family['phases'] if p['phase']=='retained-final-0.2.0-direct-legacy-upgrade-and-two-reports']
        if len(phases)!=1 or phases[0]['status']!='PASS' or phases[0]['reportsObserved']<2:raise ValueError('Final reports not proved for both families')
        proof=phases[0]['archive']
        if proof!={**{k:manifest['package'][k] for k in ('sha256','bytes','files')},'archiveReopened':True,'exactControlAndData':True}:raise ValueError('Final pre-install archive binding mismatch')
    if manifest['status']!='NATIVE_FIXTURE_QUALIFIED_NOT_RELEASED' or manifest['postSuccessRollback']!='UNQUALIFIED':raise ValueError('Final disposition mismatch')
    spec=importlib.util.spec_from_file_location('retained_deb_builder',Path(__file__).with_name('build-linux-deb.py'))
    builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
    measured,archives=builder.read_package(directory/FINAL_NAME)
    if measured!={k:manifest['package'][k] for k in ('sha256','bytes')}:raise ValueError('Retained final package changed')
    if b'Package: vmpower-agent\nVersion: 0.2.0\nArchitecture: amd64\n' not in archives['control.tar.gz']['control'][0]:raise ValueError('Final package identity mismatch')
    payload=archives['data.tar.gz'];raw=payload['usr/lib/vmpower-agent/bundle/bundle-manifest.json'][0];bundle=json.loads(raw)
    if hashlib.sha256(raw).hexdigest()!=manifest['package']['bundleManifestSha256']:raise ValueError('Retained bundle manifest mismatch')
    if any(manifest[k]!=bundle[k] for k in ('applicationVersion','nodeVersion','runtimeArchiveSha256','sourceLockSha256')):raise ValueError('Retained provenance mismatch')
    licenses=[name for name in sorted(bundle['files']) if Path(name).name.lower().startswith(('license','copying'))]
    if manifest['licensesIncluded']!=licenses or 'runtime/LICENSE' not in licenses:raise ValueError('Retained license inventory mismatch')
    print(json.dumps({'retainedFinalPackage':'PASS','bytes':measured['bytes'],'sha256':measured['sha256'],'release':'UNPUBLISHED','customerDelivery':'UNRUN'}))
    return manifest


def qualify(native,out,installer,hashes,result):
    native.guard();out=native.require_output(out)
    legacy=verify_legacy(out/'legacy-debs')
    require_absent(native)
    owned=json.loads((out/'owned.json').read_text())
    assert owned.get('dpkgPackage')==PACKAGE and owned['paths']==[str(p) for p in native.MANAGED+EXTRA_PATHS]
    spec=importlib.util.spec_from_file_location('native_deb_builder',native.ROOT/'scripts/build-linux-deb.py')
    builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
    identity=final_identity()
    if json.loads((out/'evidence/prepared.json').read_text())['head']!=identity['source']:raise ValueError('Prepared source differs from final identity')
    manifest=final_bundle(builder,out,hashes['bundle'],native.ROOT)
    retained=out/'evidence/final-linux';retained.mkdir(mode=0o755)
    final_path=retained/FINAL_NAME
    final_receipt=builder.build(out/'bundle',hashes['bundle'],'0.2.0',final_path)
    final_path.chmod(0o444)
    packages=out/'deb-packages';packages.mkdir()
    receipts=result['packages']
    receipts['final']=final_receipt
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
        phases=native.Phases(out,'package-'+name)
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
        # Keep every adverse rc phase above. Qualify the exact retained final
        # archive separately from each cached legacy family; never run Node10.
        install_legacy(name);native.command('systemctl','daemon-reload')
        assert package_state(native)=='installed|0.1' and hashlib.sha256(launcher.read_bytes()).hexdigest()==LEGACY[name]['binarySha256']
        native.command('systemctl','is-active','--quiet','vmpower.service',allowed=(3,4))
        config.chmod(0o644)
        reopened=builder.verify_package(final_path,out/'bundle',hashes['bundle'],'0.2.0',final_receipt)
        native.command('dpkg','--install',str(final_path))
        assert_owned(native,hashes['bundle'],'0.2.0');assert_config(native,raw)
        native.command('systemctl','is-active','--quiet','vmpower.service',allowed=(3,4))
        native.command('systemctl','is-enabled','--quiet','vmpower.service',allowed=(1,3,4))
        before=native.state(out)['count'];native.command('systemctl','start','vmpower.service')
        installer.System().stable_child(Path('/opt/vmpower-agent/releases')/hashes['bundle']/'runtime/node')
        pid=assert_nonroot_child(native,hashes['bundle']);native.wait(lambda:native.state(out)['count']>=before+2,85)
        assert assert_nonroot_child(native,hashes['bundle'])==pid and native.state(out)['rejected']==0
        phases.append({'phase':'retained-final-0.2.0-direct-legacy-upgrade-and-two-reports','status':'PASS','archive':reopened,'reportsObserved':native.state(out)['count']-before})
        native.command('dpkg','--purge',PACKAGE);native.wait(lambda:native.dead(pid));require_absent(native)
        assert not launcher.exists() and not unit.exists() and config.read_bytes()==raw
        phases.append({'phase':'final-purge-preserves-config','status':'PASS'})
    result['finalPackage']={**identity,'status':'NATIVE_FIXTURE_QUALIFIED_NOT_RELEASED','package':final_receipt,
        'applicationVersion':manifest['applicationVersion'],'nodeVersion':manifest['nodeVersion'],'runtimeArchiveSha256':manifest['runtimeArchiveSha256'],
        'sourceLockSha256':manifest['sourceLockSha256'],'legacyFamilies':sorted(legacy),'postSuccessRollback':'UNQUALIFIED','customerOSCompatibility':'Unknown',
        'licensesIncluded':[name for name in sorted(manifest['files']) if Path(name).name.lower().startswith(('license','copying'))]}
    native.write(retained/'manifest.json',result['finalPackage'])
    result.update({'status':'PASS','packageRegistrationAbsent':True,'actualCandidateHooksExecuted':True,'customerOSCompatibility':'Unknown','retainedConfigSha256':hashlib.sha256(raw).hexdigest()})
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--verify-retained',type=Path,required=True);args=parser.parse_args()
    verify_retained(args.verify_retained)
