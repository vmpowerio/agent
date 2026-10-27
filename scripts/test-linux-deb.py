#!/usr/bin/env python3
"""Offline package bytes/hook transaction tests; no dpkg, service or binary runs."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode=True
def load(name,file):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(file))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
builder=load('deb_builder','build-linux-deb.py')
hook=load('deb_hook','linux-deb-hook.py')
fixtures=load('deb_fixtures','test-stage-distribution.py')
dpkg_fixture=load('deb_native_fixture','qualify-linux-dpkg.py')
native=load('deb_native','qualify-native-linux.py')
quarantine=load('deb_quarantine','linux-package-quarantine.py')


def ar_members(raw):
    assert raw[:8]==b'!<arch>\n';position=8;result={}
    while position<len(raw):
        header=raw[position:position+60];assert header[-2:]==b'`\n'
        size=int(header[48:58]);name=header[:16].decode().strip().rstrip('/')
        result[name]=raw[position+60:position+60+size];position+=60+size+size%2
    assert position==len(raw);return result


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.BundleTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.bundle=self.fixture.bundle();self.digest=hashlib.sha256((self.bundle/'bundle-manifest.json').read_bytes()).hexdigest()
        self.output=self.fixture.root/'agent.deb'
    def build(self,version='0.2.0~rc1'):
        return builder.build(self.bundle,self.digest,version,self.output)
    def test_real_deb_format_owns_exact_launcher_unit_and_verified_bundle(self):
        receipt=self.build();members=ar_members(self.output.read_bytes())
        self.assertEqual(list(members),['debian-binary','control.tar.gz','data.tar.gz']);self.assertEqual(members['debian-binary'],b'2.0\n')
        with tarfile.open(fileobj=io.BytesIO(members['data.tar.gz'])) as archive:
            files={m.name.lstrip('./'):archive.extractfile(m).read() for m in archive.getmembers() if m.isfile()}
            self.assertEqual(files['usr/bin/vmpower'],builder.installer.launcher_bytes(self.digest))
            self.assertEqual(files['etc/systemd/system/vmpower.service'],builder.installer.unit_bytes())
            self.assertEqual(files['usr/lib/vmpower-agent/bundle/runtime/node'],(self.bundle/'runtime/node').read_bytes())
            self.assertNotIn('etc/vmpower.conf',files)
            self.assertTrue(all(not n.startswith('opt/') for n in files))
            self.assertTrue(all(m.uid==0 and m.gid==0 and m.mtime==0 for m in archive.getmembers()))
        with tarfile.open(fileobj=io.BytesIO(members['control.tar.gz'])) as archive:
            control={m.name.lstrip('./'):archive.extractfile(m).read() for m in archive.getmembers() if m.isfile()}
            self.assertIn(b'Version: 0.2.0~rc1\nArchitecture: amd64\n',control['control'])
            self.assertEqual(len({control[n] for n in ('preinst','postinst','prerm','postrm')}),1)
            compile(control['postinst'],'generated-postinst','exec')
            self.assertEqual(hashlib.sha256(control['postinst']).hexdigest(),receipt['maintainerHookSha256'])
            checks={line.split('  ',1)[1]:line.split('  ',1)[0] for line in control['md5sums'].decode().splitlines()}
            self.assertEqual(set(checks),set(files))
            self.assertTrue(all(hashlib.md5(raw,usedforsecurity=False).hexdigest()==checks[n] for n,raw in files.items()))
    def test_deterministic_bytes_and_no_output_overwrite(self):
        self.build();original=self.output.read_bytes();second=self.fixture.root/'second.deb'
        builder.build(self.bundle,self.digest,'0.2.0~rc1',second);self.assertEqual(original,second.read_bytes())
        with self.assertRaisesRegex(ValueError,'already exists'):self.build()
        self.assertEqual(original,self.output.read_bytes())
    def test_legacy_application_version_is_not_a_package_upgrade(self):
        for version in ['0.0.2','0.1','0.2.0\nMaintainer: injected','1:0.0.2','0.2.0;command']:
            with self.subTest(version=version),self.assertRaisesRegex(ValueError,'0.2.0'):self.build(version)
        self.assertFalse(self.output.exists())
    def test_wrong_or_changed_bundle_never_produces_package(self):
        with self.assertRaises(ValueError):builder.build(self.bundle,'0'*64,'0.2.0~rc1',self.output)
        (self.bundle/'runtime/node').write_bytes(b'changed')
        with self.assertRaises(ValueError):self.build()
        self.assertFalse(self.output.exists())
    def test_capture_race_rejects_changed_member_bytes(self):
        original=builder.installer.stage.verify_bundle
        def race(root):
            result=original(root);(root/'runtime/node').write_bytes(b'changed after verification');return result
        with mock.patch.object(builder.installer.stage,'verify_bundle',side_effect=race):
            with self.assertRaisesRegex(ValueError,'during packaging'):self.build()
        self.assertFalse(self.output.exists())


class HookTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.state=self.root/'state';self.unit=self.root/'unit';self.launcher=self.root/'launcher';self.config=self.root/'config'
        self.unit.write_bytes(builder.installer.LEGACY_UNIT);self.launcher.write_bytes(b'nonexecuted legacy payload');self.launcher.chmod(0o755)
        self.raw=b'{ "key":"fixture", "secret":"fixture", "vm_id":"fixture" }\n';self.config.write_bytes(self.raw)
        for name,value in [('STATE',self.state),('UNIT',self.unit),('LAUNCHER',self.launcher),('CONFIG',self.config),('VERSION','0.2.0~rc1'),('EXPECTED','a'*64)]:
            patch=mock.patch.object(hook,name,value);patch.start();self.addCleanup(patch.stop)
        patch=mock.patch.object(hook,'LEGACY_BINARY_SHAS',{hashlib.sha256(self.launcher.read_bytes()).hexdigest()});patch.start();self.addCleanup(patch.stop)
        patch=mock.patch.object(hook,'protected',lambda p:None);patch.start();self.addCleanup(patch.stop)
        self.commands=[]
        def command(*args,allowed=(0,)):
            self.commands.append(args)
            return 0 if args[1] in ('is-active','is-enabled') else 0
        patch=mock.patch.object(hook,'command',side_effect=command);patch.start();self.addCleanup(patch.stop)
    def test_actual_preinst_snapshot_preserves_config_bytes_and_private_backups(self):
        hook.preinst();value=hook.journal();self.assertEqual(value['state'],'prepared')
        self.assertEqual(self.config.read_bytes(),self.raw)
        self.assertEqual((self.state/value['id']/'launcher').read_bytes(),b'nonexecuted legacy payload')
        self.assertEqual(stat.S_IMODE((self.state/value['id']).stat().st_mode),0o700)
        self.assertEqual(stat.S_IMODE((self.state/'pending.json').stat().st_mode),0o600)
        self.assertFalse(any(c[1] in ('stop','start') for c in self.commands))
    def test_postinst_restores_pre_unpack_files_before_qualified_installer(self):
        hook.preinst();self.unit.write_bytes(b'unpacked candidate');self.launcher.write_bytes(b'unpacked candidate')
        def activate():
            self.assertEqual(self.unit.read_bytes(),builder.installer.LEGACY_UNIT)
            self.assertEqual(self.launcher.read_bytes(),b'nonexecuted legacy payload')
            self.unit.write_bytes(builder.installer.unit_bytes());self.launcher.write_bytes(builder.installer.launcher_bytes('a'*64))
        with mock.patch.object(hook,'activate',side_effect=activate):hook.postinst()
        self.assertEqual(hook.journal()['state'],'complete');self.assertEqual(self.config.read_bytes(),self.raw)
    def test_failed_actual_postinst_restores_running_predecessor_and_reports_failure(self):
        hook.preinst()
        def fail():
            self.unit.write_bytes(b'failed candidate unit');self.launcher.write_bytes(b'failed candidate launcher');raise RuntimeError('synthetic crash')
        with mock.patch.object(hook,'activate',side_effect=fail):
            with self.assertRaisesRegex(RuntimeError,'Reinstall the retained previous deb'):hook.postinst()
        self.assertEqual(self.unit.read_bytes(),builder.installer.LEGACY_UNIT)
        self.assertEqual(self.launcher.read_bytes(),b'nonexecuted legacy payload');self.assertEqual(self.config.read_bytes(),self.raw)
        self.assertEqual(hook.journal()['state'],'runtime-restored')
        self.assertIn(('systemctl','start','vmpower.service'),self.commands)
    def test_incomplete_transaction_blocks_next_upgrade_before_mutation(self):
        hook.preinst();before=(self.state/'pending.json').read_bytes()
        with self.assertRaisesRegex(ValueError,'Unfinished'):hook.preinst()
        self.assertEqual((self.state/'pending.json').read_bytes(),before)
    def test_snapshot_tamper_is_not_used_for_rollback(self):
        hook.preinst();value=hook.journal();(self.state/value['id']/'launcher').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'snapshot mismatch'):hook.restore(value)
    def test_foreign_unit_is_rejected_before_config_and_journal_changes(self):
        self.unit.write_bytes(b'foreign unit')
        with self.assertRaisesRegex(ValueError,'Unrecognized'):hook.preinst()
        self.assertFalse(self.state.exists());self.assertEqual(self.config.read_bytes(),self.raw)
    def test_unknown_legacy_binary_is_rejected_before_package_state_changes(self):
        self.launcher.write_bytes(b'unqualified family')
        with self.assertRaisesRegex(ValueError,'legacy executable'):hook.preinst()
        self.assertFalse(self.state.exists());self.assertEqual(self.config.read_bytes(),self.raw)
    def test_first_config_environment_contract_is_private_and_validated(self):
        self.config.unlink()
        with mock.patch.dict(os.environ,{'API_KEY':'fixture','API_SECRET':'fixture','VM_ID':'fixture'}):hook.configuration()
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode),0o600)
        self.assertEqual(set(json.loads(self.config.read_bytes())),{'key','secret','vm_id'})
    def test_invalid_first_install_environment_creates_no_config(self):
        self.config.unlink()
        with mock.patch.dict(os.environ,{'API_KEY':'','API_SECRET':'fixture','VM_ID':'fixture'}):
            with self.assertRaises(ValueError):hook.configuration()
        self.assertFalse(self.config.exists())
    def test_stopped_failed_upgrade_does_not_start_predecessor(self):
        def inactive(*args,allowed=(0,)):
            self.commands.append(args)
            return 3 if args[1]=='is-active' else 1 if args[1]=='is-enabled' else 0
        with mock.patch.object(hook,'command',side_effect=inactive):
            hook.preinst()
            with mock.patch.object(hook,'activate',side_effect=RuntimeError('synthetic failure')):
                with self.assertRaises(RuntimeError):hook.postinst()
        self.assertFalse(any(c[1]=='start' for c in self.commands))
    def test_complete_transaction_is_resnapshotted_for_reconfigure(self):
        hook.preinst();value=hook.journal();value['state']='complete';hook.save(value)
        self.unit.write_bytes(builder.installer.unit_bytes());self.launcher.write_bytes(builder.installer.launcher_bytes('a'*64))
        with mock.patch.object(hook,'activate',side_effect=RuntimeError('synthetic failure')):
            with self.assertRaises(RuntimeError):hook.postinst()
        self.assertEqual(self.launcher.read_bytes(),builder.installer.launcher_bytes('a'*64))
        self.assertNotEqual(hook.journal()['id'],value['id'])


class NativeBoundaryTests(unittest.TestCase):
    def test_slice_refuses_mac_before_reading_ownership(self):
        with mock.patch.object(quarantine.sys,'platform','darwin'):
            with self.assertRaisesRegex(RuntimeError,'HOST_REFUSED'):quarantine.begin(native,Path('/not-used'),1234)
    def test_optional_package_gate_rejects_non_host_before_any_package_operation(self):
        with mock.patch.object(native.sys,'platform','darwin'),mock.patch.object(native,'command',side_effect=AssertionError('No host operations')):
            with self.assertRaisesRegex(RuntimeError,'admitted ephemeral'):native.qualify(Path('/not-used'),True)
    def test_existing_package_registration_blocks_before_configuration_inspection(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)
            with mock.patch.dict(sys.modules,{'deb_native':native}),mock.patch.object(native,'guard'),mock.patch.object(native,'require_output',return_value=out),mock.patch.object(native,'package_fixture',return_value=dpkg_fixture),mock.patch.object(dpkg_fixture,'package_state',return_value='installed|0.1'),mock.patch.object(dpkg_fixture,'verify_legacy',side_effect=AssertionError('No input/configuration inspection')):
                with self.assertRaisesRegex(RuntimeError,'not fixture-owned'):native.qualify(out,True)
            self.assertFalse((out/'owned.json').exists())
    def test_cleanup_rejects_another_package_before_system_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);(out/'owned.json').write_text(json.dumps({'dpkgPackage':'not-vmpower'}))
            with mock.patch.object(native,'guard'),mock.patch.object(native,'require_output',return_value=out),mock.patch.object(native,'package_fixture',return_value=dpkg_fixture),mock.patch.object(native,'command',side_effect=AssertionError('No host operations')):
                with self.assertRaisesRegex(RuntimeError,'ownership mismatch'):native.cleanup(out)
            self.assertTrue((out/'owned.json').exists())


if __name__=='__main__':unittest.main()
