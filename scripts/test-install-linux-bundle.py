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
    def test_default_cli_has_no_apply_authority(self):
        result=subprocess.run([sys.executable,str(Path(__file__).with_name('install-linux-bundle.py')),'install'],capture_output=True)
        self.assertNotEqual(result.returncode,0);self.assertIn(b'No changes made',result.stderr)


if __name__=='__main__':unittest.main()
