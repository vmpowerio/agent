#!/usr/bin/env python3
"""Offline native harness regressions: no downloads, sockets, users or services."""
import hashlib,importlib.util,io,json,os,ssl,stat,subprocess,sys,tempfile,unittest,urllib.error
from contextlib import contextmanager
from pathlib import Path,PurePosixPath,PureWindowsPath
from unittest import mock
from types import SimpleNamespace
sys.dont_write_bytecode=True
def load(name,file):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(file))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
prepare=load('prepare','prepare-native-inputs.py')
import native_fixture_paths as fixture_paths

@contextmanager
def checkout_fixture():
    # A disposable default-checkout layout. This does not load or execute code.
    with tempfile.TemporaryDirectory() as d:
        root=Path(d).resolve();helper=root/'agent/agent/scripts/native_fixture_paths.py'
        helper.parent.mkdir(parents=True);helper.write_bytes(b'# nonexecuted helper fixture\n')
        directory=root/'_temp';directory.mkdir()
        with mock.patch.object(fixture_paths,'__file__',str(helper)),mock.patch.dict(os.environ,{'RUNNER_TEMP':str(directory)}):
            yield directory,helper

class RunnerPathTests(unittest.TestCase):
    def test_actual_host_layouts_derive_temp_without_environment(self):
        for helper,expected in [(PurePosixPath('/home/runner/work/agent/agent/scripts/native_fixture_paths.py'),PurePosixPath('/home/runner/work/_temp')),(PureWindowsPath('D:\\a\\agent\\agent\\scripts\\native_fixture_paths.py'),PureWindowsPath('D:\\a\\_temp'))]:
            with self.subTest(helper=helper):self.assertEqual(fixture_paths.checkout_temp(helper),expected)
    def test_ambiguous_or_traversing_environment_rejected(self):
        with checkout_fixture() as (directory,helper):
            for value in ['', '_temp','../_temp','/tmp/../_temp','/tmp/./_temp','/tmp//_temp','//host/_temp','/tmp/other','/tmp/_temp\n','file:///tmp/_temp','C:relative\\_temp','D:\\a\\..\\_temp','D:/a/_temp','\\\\host\\share\\_temp','\\\\?\\D:\\a\\_temp',str(directory/'..'/'_temp')]:
                with self.subTest(value=value),mock.patch.dict(os.environ,{'RUNNER_TEMP':value}),self.assertRaises(ValueError):fixture_paths.runner_output()
            with mock.patch.dict(os.environ,{},clear=True),self.assertRaises(ValueError):fixture_paths.runner_output()
    def test_output_mismatch_is_rejected(self):
        with checkout_fixture() as (directory,helper):
            expected=directory/'vmpower-native';self.assertEqual(fixture_paths.require_output(expected),expected)
            for value in [directory/'other',Path('vmpower-native')]:
                with self.assertRaisesRegex(ValueError,'Fixed runner'):fixture_paths.require_output(value)
    def test_preparation_rejects_normalized_environment_before_creating_output(self):
        with checkout_fixture() as (directory,helper):
            out=directory/'vmpower-native'
            env={'RUNNER_TEMP':str(directory/'..'/'_temp'),'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted'}
            with mock.patch.dict(os.environ,env),mock.patch.object(prepare.platform,'system',return_value='Linux'),mock.patch.object(prepare.platform,'machine',return_value='x86_64'),mock.patch.object(prepare,'download',side_effect=AssertionError('No download allowed')):
                with self.assertRaises(ValueError):prepare.main(out)
            self.assertFalse(out.exists())
    def test_alternate_existing_temp_cannot_select_output(self):
        with checkout_fixture() as (directory,helper):
            alternate=directory.parent/'other/_temp';alternate.mkdir(parents=True)
            with mock.patch.dict(os.environ,{'RUNNER_TEMP':str(alternate)}):
                with self.assertRaises(ValueError):fixture_paths.require_output(alternate/'vmpower-native')
            self.assertEqual(list(alternate.iterdir()),[])
    def test_preparation_rejects_alternate_root_before_output_or_download(self):
        with checkout_fixture() as (directory,helper):
            alternate=directory.parent/'other/_temp';alternate.mkdir(parents=True);out=alternate/'vmpower-native'
            env={'RUNNER_TEMP':str(alternate),'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted'}
            with mock.patch.dict(os.environ,env),mock.patch.object(prepare.platform,'system',return_value='Linux'),mock.patch.object(prepare.platform,'machine',return_value='x86_64'),mock.patch.object(prepare,'download',side_effect=AssertionError('No download allowed')):
                with self.assertRaises(ValueError):prepare.main(out)
            self.assertFalse(out.exists())
    def test_unexpected_loaded_helper_layout_rejected(self):
        with checkout_fixture() as (directory,helper):
            changed=helper.with_name('other.py');helper.rename(changed)
            with mock.patch.object(fixture_paths,'__file__',str(changed)):
                with self.assertRaises(ValueError):fixture_paths.runner_output()
    def test_hardlinked_loaded_helper_rejected(self):
        with checkout_fixture() as (directory,helper):
            os.link(helper,helper.with_name('alias.py'))
            with self.assertRaises(ValueError):fixture_paths.runner_output()

@unittest.skipIf(os.name=='nt','POSIX symlink and Linux harness entrypoint controls')
class PosixRunnerPathTests(unittest.TestCase):
    def test_symlinked_runner_temp_rejected(self):
        with checkout_fixture() as (directory,helper):
            real=directory.with_name('real');directory.rename(real);directory.symlink_to(real,target_is_directory=True)
            with self.assertRaisesRegex(ValueError,'Linked'):fixture_paths.runner_output()
            self.assertEqual(list(real.iterdir()),[])
    def test_cleanup_rejects_environment_alias_before_journal_read(self):
        native=load('native_cleanup_paths','qualify-native-linux.py')
        with checkout_fixture() as (directory,helper):
            out=directory/'vmpower-native'
            with mock.patch.dict(os.environ,{'RUNNER_TEMP':str(directory/'..'/'_temp')}),mock.patch.object(native,'guard'),mock.patch.object(native,'command',side_effect=AssertionError('No system calls allowed')):
                with self.assertRaises(ValueError):native.cleanup(out)
    def test_qualification_rejects_environment_alias_before_host_inspection(self):
        native=load('native_qualify_paths','qualify-native-linux.py')
        with checkout_fixture() as (directory,helper):
            out=directory/'vmpower-native'
            with mock.patch.dict(os.environ,{'RUNNER_TEMP':str(directory/'..'/'_temp')}),mock.patch.object(native,'guard'),mock.patch.object(native,'MANAGED',[]),mock.patch.object(native,'command',side_effect=AssertionError('No system calls allowed')):
                with self.assertRaises(ValueError):native.qualify(out)
    def test_symlinked_loaded_helper_rejected(self):
        with checkout_fixture() as (directory,helper):
            original=helper.with_name('original.py');helper.rename(original);helper.symlink_to(original)
            with self.assertRaises(ValueError):fixture_paths.runner_output()
    def test_symlinked_checkout_ancestor_rejected(self):
        with checkout_fixture() as (directory,helper):
            checkout=helper.parent.parent;original=checkout.with_name('original');checkout.rename(original);checkout.symlink_to(original,target_is_directory=True)
            with self.assertRaises(ValueError):fixture_paths.runner_output()
    def test_cleanup_rejects_alternate_root_before_journal_read(self):
        native=load('native_alternate_cleanup','qualify-native-linux.py')
        with checkout_fixture() as (directory,helper):
            alternate=directory.parent/'other/_temp';out=alternate/'vmpower-native';out.mkdir(parents=True);(out/'owned.json').write_text('{}')
            with mock.patch.dict(os.environ,{'RUNNER_TEMP':str(alternate)}),mock.patch.object(native,'guard'),mock.patch.object(native.json,'loads',side_effect=AssertionError('Must not read foreign journal')),mock.patch.object(native,'command',side_effect=AssertionError('No system calls allowed')):
                with self.assertRaises(ValueError):native.cleanup(out)
    def test_qualification_rejects_alternate_root_before_host_inspection(self):
        native=load('native_alternate_qualify','qualify-native-linux.py')
        with checkout_fixture() as (directory,helper):
            alternate=directory.parent/'other/_temp';alternate.mkdir(parents=True)
            with mock.patch.dict(os.environ,{'RUNNER_TEMP':str(alternate)}),mock.patch.object(native,'guard'),mock.patch.object(native,'MANAGED',[]),mock.patch.object(native,'command',side_effect=AssertionError('No system calls allowed')):
                with self.assertRaises(ValueError):native.qualify(alternate/'vmpower-native')

class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.destination=Path(self.temp.name)/'vendor.zip';self.raw=b'pinned public fixture'
        self.url='https://nssm.cc/ci/nssm-2.24-101-g897c7ad.zip'
        self.digest=hashlib.sha256(self.raw).hexdigest()
        self.opener=mock.Mock();self.sleep=mock.patch.object(prepare.time,'sleep').start();self.addCleanup(mock.patch.stopall)
        mock.patch.object(prepare.urllib.request,'build_opener',return_value=self.opener).start()
    def error(self,code):return urllib.error.HTTPError(self.url,code,'synthetic status',{},io.BytesIO(b''))
    def run_download(self):prepare.download(self.url,self.destination,self.digest)
    def test_transient_503_then_exact_pinned_success(self):
        self.opener.open.side_effect=[self.error(503),io.BytesIO(self.raw)]
        self.run_download();self.assertEqual(self.destination.read_bytes(),self.raw)
        self.assertEqual(self.opener.open.call_args_list,[mock.call(self.url,timeout=60)]*2)
        self.sleep.assert_called_once_with(2)
    def test_bounded_429_and_503_exhaustion(self):
        self.opener.open.side_effect=[self.error(429),self.error(503),self.error(503)]
        with self.assertRaisesRegex(RuntimeError,'PINNED_DOWNLOAD_HTTP_503'):self.run_download()
        self.assertEqual(self.opener.open.call_count,3);self.assertEqual(self.sleep.call_args_list,[mock.call(2),mock.call(4)])
        self.assertFalse(self.destination.exists())
    def test_transient_connection_error_then_success(self):
        self.opener.open.side_effect=[urllib.error.URLError(ConnectionResetError()),io.BytesIO(self.raw)]
        self.run_download();self.assertEqual(self.opener.open.call_count,2)
    def test_tls_failure_is_not_retried(self):
        self.opener.open.side_effect=urllib.error.URLError(ssl.SSLCertVerificationError('synthetic certificate rejection'))
        with self.assertRaisesRegex(RuntimeError,'TRANSPORT_REJECTED'):self.run_download()
        self.assertEqual(self.opener.open.call_count,1);self.sleep.assert_not_called()
    def test_404_is_not_retried(self):
        self.opener.open.side_effect=self.error(404)
        with self.assertRaisesRegex(RuntimeError,'HTTP_404'):self.run_download()
        self.assertEqual(self.opener.open.call_count,1);self.sleep.assert_not_called()
    def test_hash_failure_is_not_retried_or_written(self):
        self.opener.open.return_value=io.BytesIO(b'unapproved bytes')
        with self.assertRaisesRegex(ValueError,'archive mismatch'):self.run_download()
        self.assertEqual(self.opener.open.call_count,1);self.assertFalse(self.destination.exists());self.sleep.assert_not_called()
    def test_redirect_cannot_switch_recipient(self):
        with self.assertRaisesRegex(ValueError,'Unexpected vendor redirect'):
            prepare.NoRedirect().redirect_request(None,None,302,'',{},'https://mirror.invalid/archive.zip')
        self.opener.open.side_effect=ValueError('Unexpected vendor redirect')
        with self.assertRaises(ValueError):self.run_download()
        self.assertEqual(self.opener.open.call_count,1);self.sleep.assert_not_called()

@unittest.skipIf(os.name=='nt','POSIX fixture filesystem; Windows has separate ACL tests')
class ServiceFixtureTests(unittest.TestCase):
    def setUp(self):
        self.native=load('native','qualify-native-linux.py');fixtures=load('fixtures','test-stage-distribution.py')
        self.fixture=fixtures.BundleTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.bundle=self.fixture.bundle();self.expected=hashlib.sha256((self.bundle/'bundle-manifest.json').read_bytes()).hexdigest()
        self.destination=self.fixture.root/'public-fixture';self.probe=self.fixture.root/'probe.cjs';self.probe.write_bytes(b'// nonexecuted probe fixture\n')
    def test_private_source_ancestors_unchanged_and_public_copy_verified(self):
        self.fixture.root.chmod(0o700);before=self.fixture.root.stat().st_mode
        result=self.native.service_fixture(self.bundle,self.expected,self.probe,self.destination)
        self.assertEqual(before,self.fixture.root.stat().st_mode)
        self.assertEqual(result['manifestSha256'],self.expected)
        self.assertEqual((self.destination/'native-egress-probe.cjs').read_bytes(),self.probe.read_bytes())
        for p in [self.destination,*self.destination.rglob('*')]:
            self.assertFalse(p.is_symlink());self.assertFalse(stat.S_IMODE(p.stat().st_mode)&0o022)
            self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o755 if p.is_dir() or p==self.destination/'bundle/runtime/node' else 0o644)
        self.assertEqual((self.bundle/'bundle-manifest.json').read_bytes(),(self.destination/'bundle/bundle-manifest.json').read_bytes())
    def test_occupied_destination_never_changed(self):
        self.destination.mkdir();sentinel=self.destination/'sentinel';sentinel.write_bytes(b'untouched')
        with self.assertRaises(FileExistsError):self.native.service_fixture(self.bundle,self.expected,self.probe,self.destination)
        self.assertEqual(list(self.destination.iterdir()),[sentinel]);self.assertEqual(sentinel.read_bytes(),b'untouched')
    def test_changed_copy_manifest_remains_private(self):
        with self.assertRaisesRegex(ValueError,'manifest mismatch'):self.native.service_fixture(self.bundle,'0'*64,self.probe,self.destination)
        self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode),0o700)
    def test_payload_and_manifest_swapped_during_copy_are_rejected(self):
        original=self.native.shutil.copytree;changed=[]
        def swapped(source,destination,*args,**kwargs):
            if Path(source)==self.bundle and not changed:
                app=self.bundle/'app/index.js';app.write_bytes(b'// unapproved nonexecuted fixture')
                manifest=json.loads((self.bundle/'bundle-manifest.json').read_text())
                manifest['files']['app/index.js']=hashlib.sha256(app.read_bytes()).hexdigest()
                (self.bundle/'bundle-manifest.json').write_text(json.dumps(manifest));changed.append(True)
            return original(source,destination,*args,**kwargs)
        with mock.patch.object(self.native.shutil,'copytree',side_effect=swapped):
            with self.assertRaisesRegex(ValueError,'manifest mismatch'):self.native.service_fixture(self.bundle,self.expected,self.probe,self.destination)
        self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode),0o700)
    def test_probe_failure_reports_only_constant_code_and_exit(self):
        failure=subprocess.CompletedProcess([],126,'sensitive stdout','runuser: Permission denied; synthetic-secret')
        with mock.patch.object(self.native.subprocess,'run',return_value=failure):
            with self.assertRaises(RuntimeError) as caught:self.native.egress_probe(Path('/fixed/node'),Path('/fixed/probe'),1234)
        self.assertEqual(str(caught.exception),'EGRESS_PROBE_FAILED code=EXECUTION_DENIED returncode=126')
        self.assertNotIn('synthetic-secret',str(caught.exception))
    def test_external_probe_failure_does_not_become_pass(self):
        failure=subprocess.CompletedProcess([],1,'','Direct nonloopback denial not established: CONNECTED')
        with mock.patch.object(self.native.subprocess,'run',return_value=failure):
            with self.assertRaisesRegex(RuntimeError,'EXTERNAL_DENIAL_UNPROVEN'):self.native.egress_probe(Path('/fixed/node'),Path('/fixed/probe'),1234)
    def test_metadata_reads_only_fixed_ancestors_and_classifies_without_mutation(self):
        expected=['/','/opt','/etc','/etc/systemd','/etc/systemd/system','/usr','/usr/bin'];reads=[]
        def metadata(p):
            name=str(p);reads.append(name)
            self.assertIn(name,expected)
            if name=='/etc/systemd/system':raise FileNotFoundError()
            mode=stat.S_IFLNK|0o777 if name=='/etc/systemd' else stat.S_IFDIR|(0o775 if name=='/etc' else 0o755)
            return SimpleNamespace(st_uid=1001 if name=='/opt' else 0,st_gid=0,st_mode=mode)
        with mock.patch.object(Path,'lstat',metadata),mock.patch.object(Path,'chmod',side_effect=AssertionError('must not chmod')),mock.patch.object(Path,'read_bytes',side_effect=AssertionError('must not read content')),mock.patch.object(self.native.os,'chown',side_effect=AssertionError('must not chown')):
            rows=self.native.fixed_ancestor_metadata()
        self.assertEqual(reads,expected)
        self.assertEqual([row['classification'] for row in rows],['PROTECTED','NOT_ROOT_OWNED','GROUP_OR_WORLD_WRITABLE','LINKED','MISSING','PROTECTED','PROTECTED'])
        for row in rows:self.assertLessEqual(set(row),{'path','uid','gid','mode','classification'})
    def test_metadata_io_error_does_not_become_protected(self):
        with mock.patch.object(Path,'lstat',side_effect=PermissionError('synthetic unreadable metadata')):
            with self.assertRaises(PermissionError):self.native.fixed_ancestor_metadata()
    def test_actual_fixed_metadata_has_no_content_or_extra_paths(self):
        rows=self.native.fixed_ancestor_metadata()
        self.assertEqual([row['path'] for row in rows],list(self.native.FIXED_ANCESTORS))
        for row in rows:self.assertLessEqual(set(row),{'path','uid','gid','mode','classification'})
    @contextmanager
    def opt_fixture(self):
        # Only this disposable directory changes modes. No /opt/Mac ancestor,
        # account, service, socket, download or root identity is modified.
        directory=Path(tempfile.mkdtemp(prefix='owned-opt-',dir=self.fixture.root));directory.chmod(0o777)
        out=Path(tempfile.mkdtemp(prefix='receipt-',dir=self.fixture.root));(out/'evidence').mkdir()
        owned={'run':'fixture','paths':['fixture'],'firewall':[]}
        def state():return {'directory':True,'uid':0,'gid':0,'mode':format(stat.S_IMODE(directory.stat().st_mode),'04o')}
        with mock.patch.object(self.native,'OPT_PATH',directory),mock.patch.object(self.native,'guard'),mock.patch.object(self.native,'opt_state',side_effect=state):
            yield directory,out,owned
    def clean_result(self):return dict(status='PASS',serviceAbsent=True,childrenAbsent=True,managedFilesAbsent=True,fixtureInputsRemoved=True,firewallRulesRemoved=True,receiverStopped=True)
    def test_opt_journal_precedes_mode_change_and_restore_follows_cleanup(self):
        with self.opt_fixture() as (directory,out,owned):
            original=Path.chmod;changes=[]
            def observe(p,mode,*args,**kwargs):
                self.assertEqual(p,directory);self.assertEqual(json.loads((out/'owned.json').read_text())['optPermissions'],self.native.OPT_CHANGE)
                changes.append(mode);return original(p,mode,*args,**kwargs)
            with mock.patch.object(Path,'chmod',observe):
                self.native.normalize_opt(out,owned)
                self.assertEqual(stat.S_IMODE(directory.stat().st_mode),0o755)
                result=self.native.complete_cleanup(out,owned,self.clean_result())
            self.assertEqual(changes,[0o755,0o777]);self.assertEqual(result['optPermissions']['status'],'RESTORED')
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode),0o777)
    def test_opt_restored_after_injected_qualification_failure(self):
        with self.opt_fixture() as (directory,out,owned):
            with self.assertRaisesRegex(RuntimeError,'synthetic phase failure'):
                try:
                    self.native.normalize_opt(out,owned)
                    raise RuntimeError('synthetic phase failure')
                finally:self.native.complete_cleanup(out,owned,self.clean_result())
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode),0o777)
    def test_opt_incomplete_cleanup_never_exposes_owned_files(self):
        with self.opt_fixture() as (directory,out,owned):
            self.native.normalize_opt(out,owned)
            incomplete=self.clean_result();incomplete['childrenAbsent']=False
            with self.assertRaisesRegex(RuntimeError,'RESTORE_DEFERRED'):self.native.complete_cleanup(out,owned,incomplete)
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode),0o755)
            self.native.complete_cleanup(out,owned,self.clean_result())
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode),0o777)
    def test_opt_wrong_baselines_are_refused_without_write(self):
        for mode,uid,gid,directory in [('0755',0,0,True),('0775',0,0,True),('0777',1001,0,True),('0777',0,1001,True),('0777',0,0,False)]:
            with self.subTest(mode=mode,uid=uid,gid=gid,directory=directory),self.opt_fixture() as (target,out,owned):
                with mock.patch.object(self.native,'opt_state',return_value={'mode':mode,'uid':uid,'gid':gid,'directory':directory}),mock.patch.object(Path,'chmod',side_effect=AssertionError('must not chmod')):
                    with self.assertRaisesRegex(RuntimeError,'UNEXPECTED_OPT_BASELINE'):self.native.normalize_opt(out,owned)
                self.assertFalse((out/'owned.json').exists());self.assertEqual(stat.S_IMODE(target.stat().st_mode),0o777)
    def test_opt_ambiguous_journal_refuses_restoration(self):
        with self.opt_fixture() as (directory,out,owned):
            self.native.normalize_opt(out,owned);owned['optPermissions']['originalMode']='0755'
            with self.assertRaisesRegex(RuntimeError,'AMBIGUOUS_OPT_JOURNAL'):self.native.complete_cleanup(out,owned,self.clean_result())
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode),0o755)
    def test_opt_interrupted_before_chmod_can_reconcile_original_mode(self):
        with self.opt_fixture() as (directory,out,owned):
            with mock.patch.object(Path,'chmod',side_effect=OSError('synthetic chmod failure')):
                with self.assertRaises(OSError):self.native.normalize_opt(out,owned)
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode),0o777)
            self.native.complete_cleanup(out,json.loads((out/'owned.json').read_text()),self.clean_result())
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode),0o777)
    def test_opt_always_cleanup_reentry_does_not_repeat_chmod(self):
        with self.opt_fixture() as (directory,out,owned):
            self.native.normalize_opt(out,owned)
            self.native.complete_cleanup(out,owned,self.clean_result())
            with mock.patch.object(Path,'chmod',side_effect=AssertionError('already restored')):
                result=self.native.complete_cleanup(out,json.loads((out/'owned.json').read_text()),self.clean_result())
            self.assertEqual(result['optPermissions']['mode'],'0777')
    def test_atomic_journal_write_failure_preserves_original(self):
        with self.opt_fixture() as (directory,out,owned):
            journal=out/'owned.json';original=json.loads(json.dumps(owned));journal.write_text(json.dumps(original))
            with mock.patch.object(self.native.json,'dump',side_effect=RuntimeError('synthetic serialization failure')):
                with self.assertRaises(RuntimeError):self.native.normalize_opt(out,owned)
            self.assertEqual(json.loads(journal.read_text()),original)
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode),0o777)
            self.assertEqual(sorted(p.name for p in out.iterdir()),['evidence','owned.json'])
    def test_opt_preexisting_mismatched_journal_is_never_overwritten(self):
        with self.opt_fixture() as (directory,out,owned):
            journal=out/'owned.json';journal.write_text('{"run":"other"}')
            with self.assertRaisesRegex(RuntimeError,'AMBIGUOUS_OPT_JOURNAL'):self.native.normalize_opt(out,owned)
            self.assertEqual(journal.read_text(),'{"run":"other"}');self.assertEqual(stat.S_IMODE(directory.stat().st_mode),0o777)

@unittest.skipIf(os.name=='nt','Linux /proc receiver identity; Windows has separate native process identity checks')
class ReceiverIdentityTests(unittest.TestCase):
    def test_exact_owned_receiver_only(self):
        native=load('receiver_identity','qualify-native-linux.py')
        out=Path('/owned/vmpower-native');node=str(out/'bundle/runtime/node')
        script=str(native.ROOT/'scripts/native-report-fixture.cjs').encode()
        exact=[node.encode(),script,b'']
        self.assertTrue(native.receiver_matches(out,node,exact))
        for executable,argv in [('/other/node',exact),(node,[node.encode(),script+b'.other',b'']),
            (node,[node.encode(),script,b'extra',b'']),(node,[node.encode(),b'/other/helper',script,b'']),
            (node,[node.encode(),script,str(out/'receiver.json').encode(),b''])]:
            with self.subTest(executable=executable,argv=argv):self.assertFalse(native.receiver_matches(out,executable,argv))

if __name__=='__main__':unittest.main()
