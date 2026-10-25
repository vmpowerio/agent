#!/usr/bin/env python3
"""Offline filesystem/transaction tests. No service, account or binary executes."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

sys.dont_write_bytecode=True
def load(name,file):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(file))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
installer=load('installer','install-linux-bundle.py')
fixtures=load('fixtures','test-stage-distribution.py')


class FakeSystem:
    def __init__(self,active=False,enabled=False,fail_start=False):
        self.active=active;self.enabled=enabled;self.fail_start=fail_start;self.calls=[];self.owners=[]
    def destination(self,p): pass # Test roots are deliberately owned by the test user.
    def account(self): self.calls.append(('account',));return 12345
    def config_owner(self,p,gid): self.owners.append((p,gid))
    def stable_child(self,executable): self.calls.append(('stable-child',str(executable)))
    def call(self,*args,allowed=(0,)):
        self.calls.append(args);op=args[1]
        if op=='is-active': result=0 if self.active else 3
        elif op=='is-enabled': result=0 if self.enabled else 1
        else:
            result=0
            if op=='start':
                if self.fail_start: self.fail_start=False;raise RuntimeError('synthetic start failure')
                self.active=True
            if op=='stop':self.active=False
            if op=='enable':self.enabled=True
            if op=='disable':self.enabled=False
        if result not in allowed: raise RuntimeError('synthetic state failure')
        return result


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.BundleTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.bundle=self.fixture.bundle().resolve();self.root=(self.fixture.root/'machine').resolve();self.root.mkdir()
        self.expected=installer.stage.digest((self.bundle/'bundle-manifest.json').read_bytes())
        self.config=json.dumps({'key':'fixture "key"','secret':'$(`not-a-command`)\\\\\\','vm_id':'fixture','host':'https://invalid.example'}).encode()
        self.system=FakeSystem()
    def install(self,incoming=None):
        return installer.install(self.bundle,self.expected,self.config if incoming is None else incoming,self.root,self.system)
    def legacy(self):
        unit=self.root/'etc/systemd/system/vmpower.service';unit.parent.mkdir(parents=True);unit.write_bytes(installer.LEGACY_UNIT)
        executable=self.root/'usr/bin/vmpower';executable.parent.mkdir(parents=True);executable.write_bytes(b'old executable fixture');executable.chmod(0o755)
        conf=self.root/'etc/vmpower.conf';conf.write_bytes(self.config)
        return unit,executable,conf
    def test_fresh_install_preserves_json_values_and_restricts_permissions(self):
        result=self.install();conf=self.root/'etc/vmpower.conf'
        self.assertEqual(json.loads(conf.read_bytes()),json.loads(self.config))
        self.assertEqual(stat.S_IMODE(conf.stat().st_mode),0o640)
        self.assertEqual(self.system.owners,[(conf,12345)])
        self.assertTrue(self.system.active and self.system.enabled)
        self.assertNotIn('secret',(self.root/'usr/bin/vmpower').read_text())
        self.assertEqual(stat.S_IMODE(Path(result['rollbackFiles']).stat().st_mode),0o700)
        subprocess.run(['/bin/sh','-n',str(self.root/'usr/bin/vmpower')],check=True)
    def test_upgrade_ignores_new_or_missing_credentials_and_keeps_legacy_backup(self):
        unit,exe,conf=self.legacy();self.system.active=True;self.system.enabled=True
        result=self.install(b'{"invalid":"replacement"}')
        self.assertEqual(conf.read_bytes(),self.config)
        self.assertEqual((Path(result['rollbackFiles'])/'vmpower').read_bytes(),b'old executable fixture')
        installer.install(self.bundle,self.expected,None,self.root,self.system)
        self.assertEqual(conf.read_bytes(),self.config)
    def test_stopped_disabled_upgrade_remains_stopped_disabled(self):
        self.legacy();self.install();self.assertFalse(self.system.active);self.assertFalse(self.system.enabled)
    def test_failed_upgrade_restores_old_launcher_unit_and_running_state(self):
        unit,exe,conf=self.legacy();self.system=FakeSystem(True,True,True)
        with self.assertRaisesRegex(RuntimeError,'synthetic start'):self.install()
        self.assertEqual(unit.read_bytes(),installer.LEGACY_UNIT)
        self.assertEqual(exe.read_bytes(),b'old executable fixture')
        self.assertEqual(conf.read_bytes(),self.config)
        self.assertTrue(self.system.active and self.system.enabled)
    def test_failed_first_install_removes_service_files_but_keeps_config_and_release(self):
        self.system.fail_start=True
        with self.assertRaises(RuntimeError):self.install()
        self.assertFalse((self.root/'etc/systemd/system/vmpower.service').exists())
        self.assertFalse((self.root/'usr/bin/vmpower').exists())
        self.assertTrue((self.root/'etc/vmpower.conf').exists())
        self.assertTrue((self.root/'opt/vmpower-agent/releases'/self.expected).exists())
        self.assertFalse(self.system.active or self.system.enabled)
    def test_unstable_child_restores_previous_service(self):
        unit,exe,conf=self.legacy();self.system=FakeSystem(True,True)
        self.system.stable_child=mock.Mock(side_effect=RuntimeError('unstable child'))
        with self.assertRaisesRegex(RuntimeError,'unstable'):self.install()
        self.assertEqual(unit.read_bytes(),installer.LEGACY_UNIT)
        self.assertEqual(exe.read_bytes(),b'old executable fixture')
        self.assertTrue(self.system.active)
    def test_native_child_check_rejects_restarting_pid(self):
        responses=[SimpleNamespace(stdout=b'101\n'),SimpleNamespace(stdout=b'102\n')]
        with mock.patch.object(installer.subprocess,'run',side_effect=responses),mock.patch.object(installer.os,'readlink',return_value='/qualified/node'),mock.patch.object(installer.time,'sleep'),mock.patch.object(installer.time,'monotonic',side_effect=range(50)):
            with self.assertRaisesRegex(RuntimeError,'not stable'):installer.System().stable_child(Path('/qualified/node'))
    def test_native_child_check_rejects_wrong_executable(self):
        with mock.patch.object(installer.subprocess,'run',return_value=SimpleNamespace(stdout=b'101\n')),mock.patch.object(installer.os,'readlink',return_value='/other/node'),mock.patch.object(installer.time,'sleep'),mock.patch.object(installer.time,'monotonic',side_effect=range(50)):
            with self.assertRaisesRegex(RuntimeError,'not observed'):installer.System().stable_child(Path('/qualified/node'))
    def test_uninstall_keeps_configuration_and_releases(self):
        self.install();before=(self.root/'etc/vmpower.conf').read_bytes()
        installer.uninstall(self.root,self.system)
        self.assertEqual((self.root/'etc/vmpower.conf').read_bytes(),before)
        self.assertTrue((self.root/'opt/vmpower-agent/releases'/self.expected).exists())
        self.assertFalse((self.root/'usr/bin/vmpower').exists())
    def test_missing_invalid_and_empty_credentials_fail_before_system_calls(self):
        for value in [None,b'{}',b'null',b'{',b'{"key":"","secret":"x","vm_id":"x"}']:
            with self.assertRaises(ValueError):installer.install(self.bundle,self.expected,value,self.root,self.system)
            self.assertEqual(self.system.calls,[])
    def test_hardlinked_config_rejected_without_changing_target(self):
        self.legacy();os.link(self.root/'etc/vmpower.conf',self.root/'other')
        with self.assertRaisesRegex(ValueError,'unlinked'):self.install()
        self.assertEqual(self.system.calls,[])
    def test_symlink_release_directory_rejected_before_external_changes(self):
        prefix=self.root/'opt/vmpower-agent';prefix.mkdir(parents=True)
        outside=self.root/'outside';outside.mkdir();(prefix/'releases').symlink_to(outside)
        with self.assertRaisesRegex(ValueError,'symlink'):self.install()
        self.assertEqual(self.system.calls,[]);self.assertEqual(list(outside.iterdir()),[])
    def test_foreign_unit_rejected_without_changes(self):
        unit,exe,conf=self.legacy();unit.write_text('[Service]\nExecStart=/foreign\n')
        with self.assertRaisesRegex(ValueError,'recognized'):self.install()
        self.assertEqual(self.system.calls,[])
    def test_symlink_retained_release_is_not_followed(self):
        releases=self.root/'opt/vmpower-agent/releases';releases.mkdir(parents=True)
        (releases/self.expected).symlink_to(self.bundle)
        before=(self.bundle/'runtime/node').stat().st_mode
        with self.assertRaisesRegex(ValueError,'symlink release'):self.install()
        self.assertEqual((self.bundle/'runtime/node').stat().st_mode,before)
    def test_real_destination_guard_rejects_writable_parent(self):
        unsafe=self.root/'unsafe';unsafe.mkdir();unsafe.chmod(0o777)
        with self.assertRaisesRegex(ValueError,'root owned'):installer.System().destination(unsafe/'file')
    def test_shared_service_group_cannot_read_configuration(self):
        account=SimpleNamespace(pw_uid=12345,pw_gid=12345,pw_name='vmpower')
        group=SimpleNamespace(gr_name='vmpower',gr_mem=['another-user'])
        with mock.patch.object(installer.pwd,'getpwnam',return_value=account),mock.patch.object(installer.grp,'getgrgid',return_value=group),mock.patch.object(installer.pwd,'getpwall',return_value=[account]):
            with self.assertRaisesRegex(ValueError,'dedicated'):installer.System().account()
    def test_dedicated_nonroot_service_group_is_accepted_without_useradd(self):
        account=SimpleNamespace(pw_uid=12345,pw_gid=12345,pw_name='vmpower')
        group=SimpleNamespace(gr_name='vmpower',gr_mem=[])
        with mock.patch.object(installer.pwd,'getpwnam',return_value=account),mock.patch.object(installer.grp,'getgrgid',return_value=group),mock.patch.object(installer.pwd,'getpwall',return_value=[account]),mock.patch.object(installer.System,'call',side_effect=AssertionError('No account creation')):
            self.assertEqual(installer.System().account(),12345)
    def test_changed_bundle_and_wrong_digest_fail_before_system_calls(self):
        with self.assertRaises(ValueError):installer.install(self.bundle,'0'*64,self.config,self.root,self.system)
        (self.bundle/'app/index.js').write_text('tampered')
        with self.assertRaises(ValueError):self.install()
        self.assertEqual(self.system.calls,[])
    def test_source_swapped_during_copy_is_rejected_before_service_changes(self):
        unit,exe,conf=self.legacy();self.system=FakeSystem(True,True)
        original_copy=installer.shutil.copytree;swapped=[]
        def copy_swapped_source(src,dst,*args,**kwargs):
            if Path(src)==self.bundle and not swapped:
                app=self.bundle/'app/index.js';app.write_bytes(b'// substituted nonexecuted payload\n')
                manifest=json.loads((self.bundle/'bundle-manifest.json').read_text())
                manifest['files']['app/index.js']=installer.stage.digest(app.read_bytes())
                (self.bundle/'bundle-manifest.json').write_text(json.dumps(manifest));swapped.append(True)
            return original_copy(src,dst,*args,**kwargs)
        with mock.patch.object(installer.shutil,'copytree',side_effect=copy_swapped_source):
            with self.assertRaisesRegex(ValueError,'Copied bundle manifest'):self.install()
        self.assertEqual(self.system.calls,[('account',)])
        self.assertTrue(self.system.active and self.system.enabled)
        self.assertEqual(unit.read_bytes(),installer.LEGACY_UNIT)
        self.assertEqual(exe.read_bytes(),b'old executable fixture')
        self.assertEqual(conf.read_bytes(),self.config)
        self.assertFalse((self.root/'opt/vmpower-agent/releases'/self.expected).exists())
    def test_writable_input_is_sealed_before_publish_without_mutating_input(self):
        (self.bundle/'app/index.js').chmod(0o666);(self.bundle/'runtime/node').chmod(0o777)
        self.bundle.chmod(0o777)
        original={str(p.relative_to(self.bundle)):(p.stat().st_mode,installer.stage.digest(p.read_bytes()) if p.is_file() else None) for p in [self.bundle,*self.bundle.rglob('*')]}
        original_rename=installer.os.rename;observed=[]
        def observe_publication(src,dst,*args,**kwargs):
            staged=Path(src)
            self.assertEqual(stat.S_IMODE(staged.parent.stat().st_mode),0o700)
            for p in [staged,*staged.rglob('*')]:
                self.assertFalse(p.is_symlink())
                self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o755 if p.is_dir() or p==staged/'runtime/node' else 0o644)
            self.assertEqual(installer.stage.digest((staged/'bundle-manifest.json').read_bytes()),self.expected)
            observed.append(True);return original_rename(src,dst,*args,**kwargs)
        with mock.patch.object(installer.os,'rename',side_effect=observe_publication):self.install()
        self.assertEqual(observed,[True])
        after={str(p.relative_to(self.bundle)):(p.stat().st_mode,installer.stage.digest(p.read_bytes()) if p.is_file() else None) for p in [self.bundle,*self.bundle.rglob('*')]}
        self.assertEqual(after,original)
    def test_symlink_introduced_during_copy_cannot_touch_external_file(self):
        external=self.root/'external';external.write_bytes(b'synthetic external fixture');external.chmod(0o600)
        original_copy=installer.shutil.copytree;swapped=[]
        def copy_with_link(src,dst,*args,**kwargs):
            if Path(src)==self.bundle and not swapped:
                app=self.bundle/'app/index.js';app.unlink();app.symlink_to(external);swapped.append(True)
            return original_copy(src,dst,*args,**kwargs)
        with mock.patch.object(installer.shutil,'copytree',side_effect=copy_with_link):
            with self.assertRaisesRegex(ValueError,'symlink'):self.install()
        self.assertEqual(external.read_bytes(),b'synthetic external fixture')
        self.assertEqual(stat.S_IMODE(external.stat().st_mode),0o600)
        self.assertEqual(self.system.calls,[('account',)])
        self.assertFalse((self.root/'opt/vmpower-agent/releases'/self.expected).exists())
    def test_default_cli_has_no_apply_authority(self):
        result=subprocess.run([sys.executable,str(Path(__file__).with_name('install-linux-bundle.py')),'install'],capture_output=True)
        self.assertNotEqual(result.returncode,0);self.assertIn(b'No changes made',result.stderr)
    def test_digest_syntax_rejected_before_any_bundle_read(self):
        for value in [None,123,'a'*63,'a'*65,'A'*64,'../'+'a'*61,'/'+'a'*63,'a'*63+'\n','a'*63+'\\']:
            with self.subTest(value=value),mock.patch.object(installer.stage,'verify_bundle',side_effect=AssertionError('Bundle must not be read')):
                with self.assertRaisesRegex(ValueError,'64 lowercase'):installer.install(self.bundle,value,self.config,self.root,self.system)
        self.assertEqual(self.system.calls,[])
    def test_new_configuration_is_private_before_service_group_assignment(self):
        original=self.system.config_owner;observed=[]
        def owner(p,gid):
            observed.append(stat.S_IMODE(p.stat().st_mode))
            self.assertEqual(p.read_bytes(),installer.config_bytes(None,self.config))
            self.assertEqual(observed[-1],0o600)
            return original(p,gid)
        self.system.config_owner=owner;self.install()
        self.assertEqual(observed,[0o600]);self.assertEqual(stat.S_IMODE((self.root/'etc/vmpower.conf').stat().st_mode),0o640)
    def test_group_assignment_failure_leaves_new_configuration_private(self):
        self.system.config_owner=mock.Mock(side_effect=RuntimeError('synthetic ownership failure'))
        with self.assertRaisesRegex(RuntimeError,'ownership failure'):self.install()
        conf=self.root/'etc/vmpower.conf'
        self.assertEqual(stat.S_IMODE(conf.stat().st_mode),0o600)
        self.assertEqual(json.loads(conf.read_bytes()),json.loads(self.config))
        self.assertEqual(self.system.calls,[('account',)])
    def test_symlink_bundle_root_rejected_before_read_or_system_changes(self):
        alias=self.fixture.root/'input-alias';alias.symlink_to(self.bundle,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'real directory'):installer.install(alias,self.expected,self.config,self.root,self.system)
        self.assertEqual(self.system.calls,[]);self.assertEqual(list(self.root.iterdir()),[])
    def test_legitimate_arbitrary_bundle_input_name_is_preserved(self):
        chosen=self.fixture.root/'another selected bundle';self.bundle.rename(chosen);self.bundle=chosen
        result=self.install();self.assertEqual(result['release'],self.expected)
        self.assertEqual(json.loads((self.root/'etc/vmpower.conf').read_bytes()),json.loads(self.config))


if __name__=='__main__':unittest.main()
