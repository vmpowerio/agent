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
    def test_final_archive_reopened_independently_with_exact_control_payload_and_license(self):
        receipt=self.build('0.2.0')
        proof=builder.verify_package(self.output,self.bundle,self.digest,'0.2.0',receipt)
        self.assertGreater(proof['bytes'],0);self.assertEqual(proof['bytes'],self.output.stat().st_size)
        self.assertEqual(proof['sha256'],hashlib.sha256(self.output.read_bytes()).hexdigest())
        self.assertTrue(proof['archiveReopened'] and proof['exactControlAndData'])
        _,archives=builder.read_package(self.output)
        self.assertIn('usr/lib/vmpower-agent/bundle/runtime/LICENSE',archives['data.tar.gz'])
        self.assertNotIn('etc/vmpower.conf',archives['data.tar.gz'])
    def test_zero_size_receipt_and_rc_archive_cannot_qualify_as_final(self):
        receipt=self.build()
        with self.assertRaisesRegex(ValueError,'differs'):builder.verify_package(self.output,self.bundle,self.digest,'0.2.0',receipt)
        with self.assertRaisesRegex(ValueError,'size/hash'):builder.verify_package(self.output,self.bundle,self.digest,'0.2.0~rc1',{**receipt,'bytes':0})
    def test_empty_truncated_trailing_or_changed_archive_refused(self):
        receipt=self.build('0.2.0');original=self.output.read_bytes()
        for raw in (b'',original[:50],original[:-1],original+b'extra',b'badmagic'+original[8:]):
            with self.subTest(size=len(raw)):
                self.output.write_bytes(raw)
                with self.assertRaises(ValueError):builder.verify_package(self.output,self.bundle,self.digest,'0.2.0',receipt)
    def test_rehashed_unexpected_config_or_hook_is_not_an_accepted_archive(self):
        receipt=self.build('0.2.0');payload,controls=builder.package_files(self.bundle,self.digest,'0.2.0')
        variants=[({**payload,'etc/vmpower.conf':(b'fixture only',0o600)},controls),(payload,{**controls,'postinst':(b'altered hook',0o755)})]
        for data,control in variants:
            raw=builder.ar_bytes([('debian-binary',b'2.0\n'),('control.tar.gz',builder.tar_bytes(control)),('data.tar.gz',builder.tar_bytes(data))]);self.output.write_bytes(raw)
            updated={**receipt,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
            with self.assertRaisesRegex(ValueError,'differs'):builder.verify_package(self.output,self.bundle,self.digest,'0.2.0',updated)
    def test_archive_links_and_excessive_member_size_refused_without_extraction(self):
        for kind in ('link','oversize'):
            stream=io.BytesIO()
            with tarfile.open(fileobj=stream,mode='w:gz') as archive:
                entry=tarfile.TarInfo('./unexpected')
                if kind=='link':entry.type=tarfile.SYMTYPE;entry.linkname='/outside'
                else:entry.size=512*1024*1024+1
                archive.addfile(entry)
            self.output.write_bytes(builder.ar_bytes([('debian-binary',b'2.0\n'),('control.tar.gz',stream.getvalue()),('data.tar.gz',stream.getvalue())]))
            with self.subTest(kind=kind),self.assertRaisesRegex(ValueError,'links|bounds'):builder.read_package(self.output)
    def test_linked_package_and_oversize_file_refused_before_open(self):
        self.build('0.2.0');alias=self.output.with_name('alias.deb');alias.symlink_to(self.output)
        with self.assertRaisesRegex(ValueError,'single-link'):builder.read_package(alias)
        alias.unlink();os.link(self.output,alias)
        with self.assertRaisesRegex(ValueError,'single-link'):builder.read_package(self.output)
        alias.unlink()
        with self.output.open('wb') as handle:handle.truncate(160*1024*1024+1)
        with mock.patch.object(builder.os,'open',side_effect=AssertionError('Must not open excessive input')):
            with self.assertRaisesRegex(ValueError,'single-link'):builder.read_package(self.output)
    def test_final_bundle_requires_original_runtime_source_and_unmodified_application(self):
        manifest=dpkg_fixture.final_bundle(builder,self.fixture.root,self.digest,self.fixture.repo)
        self.assertEqual(manifest['nodeVersion'],'24.22.1')
        (self.fixture.repo/'build/index.js').write_text('different compiled application')
        with self.assertRaisesRegex(ValueError,'unmodified'):dpkg_fixture.final_bundle(builder,self.fixture.root,self.digest,self.fixture.repo)
    def test_synthetic_fixture_cannot_be_retained_even_with_consistent_manifest_hash(self):
        file=self.bundle/'bundle-manifest.json';manifest=json.loads(file.read_bytes());manifest['qualification']='SYNTHETIC LIFECYCLE FIXTURE; NEVER PUBLISH'
        file.write_text(json.dumps(manifest));digest=hashlib.sha256(file.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError,'unmodified'):dpkg_fixture.final_bundle(builder,self.fixture.root,digest,self.fixture.repo)
    def test_original_runtime_archive_must_still_match_pin(self):
        with self.fixture.archive.open('ab') as handle:handle.write(b'changed archive')
        with self.assertRaisesRegex(ValueError,'pinned'):dpkg_fixture.final_bundle(builder,self.fixture.root,self.digest,self.fixture.repo)


class RetainedGateTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.BundleTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.bundle=self.fixture.bundle();self.out=self.fixture.root;self.evidence=self.out/'evidence';self.directory=self.evidence/'final-linux';self.directory.mkdir(parents=True)
        digest=hashlib.sha256((self.bundle/'bundle-manifest.json').read_bytes()).hexdigest()
        self.package=builder.build(self.bundle,digest,'0.2.0',self.directory/dpkg_fixture.FINAL_NAME)
        self.identity={'repository':'vmpowerio/agent','source':'a'*40,'tree':'b'*40,'runId':'123','attempt':'1','job':'linux-native'}
        bundle=json.loads((self.bundle/'bundle-manifest.json').read_bytes())
        self.manifest={**self.identity,'status':'NATIVE_FIXTURE_QUALIFIED_NOT_RELEASED','package':self.package,
            **{k:bundle[k] for k in ('applicationVersion','nodeVersion','runtimeArchiveSha256','sourceLockSha256')},
            'legacyFamilies':sorted(dpkg_fixture.LEGACY),'postSuccessRollback':'UNQUALIFIED','customerOSCompatibility':'Unknown','licensesIncluded':['runtime/LICENSE']}
        proof=builder.verify_package(self.directory/dpkg_fixture.FINAL_NAME,self.bundle,digest,'0.2.0',self.package)
        families=[{'name':name,'phases':[{'phase':'retained-final-0.2.0-direct-legacy-upgrade-and-two-reports','status':'PASS','archive':proof,'reportsObserved':2}]} for name in dpkg_fixture.LEGACY]
        self.lifecycle={'status':'PASS','head':self.identity['source'],'realPackageOwnership':{'status':'PASS','finalPackage':self.manifest,'packages':{'final':self.package},'families':families}}
        self.write(self.directory/'manifest.json',self.manifest);self.write(self.evidence/'lifecycle-linux.json',self.lifecycle)
        self.cleanup={'status':'PASS',**{k:True for k in ('serviceAbsent','childrenAbsent','managedFilesAbsent','packageRegistrationAbsent')}}
        self.write(self.evidence/'cleanup-linux.json',self.cleanup);self.write(self.evidence/'supervisor-outcome-linux.json',{'status':'PASS'});self.write(self.out/'isolation.json',{'state':'REMOVED'})
        import native_fixture_paths
        for patch in (mock.patch.object(native_fixture_paths,'require_output',return_value=self.out),mock.patch.object(dpkg_fixture,'final_identity',return_value=self.identity)):
            patch.start();self.addCleanup(patch.stop)
    def write(self,file,value):file.write_text(json.dumps(value))
    def verify(self):
        with mock.patch('sys.stdout',new_callable=io.StringIO) as output:
            result=dpkg_fixture.verify_retained(self.out)
            self.assertEqual(set(json.loads(output.getvalue())),{'retainedFinalPackage','bytes','sha256','release','customerDelivery'})
            return result
    def test_success_gate_keeps_exact_final_bytes_and_explicit_unreleased_limits(self):
        before=(self.directory/dpkg_fixture.FINAL_NAME).read_bytes();self.assertEqual(self.verify(),self.manifest)
        self.assertEqual((self.directory/dpkg_fixture.FINAL_NAME).read_bytes(),before)
        self.assertEqual(self.manifest['postSuccessRollback'],'UNQUALIFIED')
    def test_any_cleanup_or_quarantine_failure_blocks_retention(self):
        for key in ('serviceAbsent','childrenAbsent','managedFilesAbsent','packageRegistrationAbsent'):
            self.write(self.evidence/'cleanup-linux.json',{**self.cleanup,key:False})
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'cleanup'):self.verify()
        self.write(self.evidence/'cleanup-linux.json',self.cleanup)
        for file,value in ((self.evidence/'supervisor-outcome-linux.json',{'status':'FAIL'}),(self.out/'isolation.json',{'state':'INSTALLED'})):
            original=file.read_bytes();self.write(file,value)
            with self.assertRaisesRegex(ValueError,'not complete'):self.verify()
            file.write_bytes(original)
    def test_source_drift_and_missing_family_report_block_retention(self):
        self.write(self.directory/'manifest.json',{**self.manifest,'source':'c'*40})
        with self.assertRaisesRegex(ValueError,'source/job'):self.verify()
        self.write(self.directory/'manifest.json',self.manifest)
        self.lifecycle['realPackageOwnership']['families'][1]['phases'][0]['reportsObserved']=1
        self.write(self.evidence/'lifecycle-linux.json',self.lifecycle)
        with self.assertRaisesRegex(ValueError,'reports'):self.verify()
    def test_zero_size_or_changed_final_archive_blocks_retention(self):
        file=self.directory/dpkg_fixture.FINAL_NAME;original=file.read_bytes()
        for raw in (b'',original+b'extra'):
            file.write_bytes(raw)
            with self.assertRaises(ValueError):self.verify()
    def test_extra_config_or_unknown_manifest_fields_block_retention(self):
        extra=self.directory/'config.json';extra.write_text('synthetic only')
        with self.assertRaisesRegex(ValueError,'inventory'):self.verify()
        extra.unlink();self.write(self.directory/'manifest.json',{**self.manifest,'config':'synthetic only'})
        with self.assertRaisesRegex(ValueError,'schema'):self.verify()
    def test_preinstall_proof_cannot_use_zero_byte_receipt(self):
        self.lifecycle['realPackageOwnership']['families'][0]['phases'][0]['archive']['bytes']=0
        self.write(self.evidence/'lifecycle-linux.json',self.lifecycle)
        with self.assertRaisesRegex(ValueError,'pre-install'):self.verify()
    def test_manifest_cannot_misstate_included_licenses(self):
        self.manifest['licensesIncluded']=[]
        self.write(self.directory/'manifest.json',self.manifest);self.write(self.evidence/'lifecycle-linux.json',self.lifecycle)
        with self.assertRaisesRegex(ValueError,'license'):self.verify()
    def test_linked_manifest_refused(self):
        file=self.directory/'manifest.json';target=self.out/'other.json';file.rename(target);file.symlink_to(target)
        with self.assertRaisesRegex(ValueError,'regular'):self.verify()


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
    def test_final_identity_rejects_wrong_job_repo_source_and_noncanonical_run(self):
        env={'QUALIFIED_SOURCE_SHA':'a'*40,'QUALIFIED_SOURCE_TREE':'b'*40,'GITHUB_REPOSITORY':'vmpowerio/agent','GITHUB_JOB':'linux-native','GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1'}
        with mock.patch.dict(os.environ,env,clear=True):self.assertEqual(dpkg_fixture.final_identity()['runId'],'123')
        for key,value in [('GITHUB_JOB','windows-native'),('GITHUB_REPOSITORY','other/agent'),('QUALIFIED_SOURCE_SHA',''),('QUALIFIED_SOURCE_TREE','c'*39),('GITHUB_RUN_ID','00123'),('GITHUB_RUN_ATTEMPT','0')]:
            with self.subTest(key=key),mock.patch.dict(os.environ,{**env,key:value},clear=True),self.assertRaises((ValueError,RuntimeError)):dpkg_fixture.final_identity()
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
