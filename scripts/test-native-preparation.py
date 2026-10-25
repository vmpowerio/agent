#!/usr/bin/env python3
"""Offline native harness regressions: no downloads, sockets, users or services."""
import hashlib,importlib.util,io,json,os,ssl,stat,subprocess,sys,tempfile,unittest,urllib.error
from pathlib import Path
from unittest import mock
sys.dont_write_bytecode=True
def load(name,file):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(file))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
prepare=load('prepare','prepare-native-inputs.py')

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

if __name__=='__main__':unittest.main()
