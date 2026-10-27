#!/usr/bin/env python3
"""No root/systemd/firewall/network operations: source mocks and real child tests."""
import errno,importlib.util,io,json,os,signal,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from unittest import mock
sys.dont_write_bytecode=True
import native_linux_commands as commands
def load(name,file):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(file))
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);return module
isolation=load('tested_slice','linux-package-quarantine.py')
native=load('tested_native','qualify-native-linux.py')

class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.patch=mock.patch.object(commands,'EVIDENCE',self.root);self.patch.start();self.addCleanup(self.patch.stop)
        self.filepatch=mock.patch.object(commands,'OPERATION_FILE','operation-linux.json');self.filepatch.start();self.addCleanup(self.filepatch.stop)
    def receipt(self):return json.loads((self.root/'operation-linux.json').read_text())
    def test_real_delayed_success(self):
        result=commands.run(sys.executable,'-c','import time;time.sleep(.08);print("ok")',timeout=2)
        self.assertEqual(result.stdout,'ok\n');self.assertEqual(self.receipt()['classification'],'SUCCESS')
    def test_nonzero_closed_labels_hide_stderr_and_arguments(self):
        with self.assertRaises(commands.CommandFailure) as caught:
            commands.run(sys.executable,'-c','import sys;print("dpkg frontend lock was locked synthetic-secret",file=sys.stderr);sys.exit(2)')
        self.assertEqual(caught.exception.record['exitCode'],2)
        self.assertEqual(caught.exception.record['diagnosticLabels'],['DPKG_LOCK_HELD'])
        self.assertNotIn('synthetic-secret',json.dumps(self.receipt())+str(caught.exception))
    def test_real_parent_and_descendant_killed_on_timeout(self):
        marker=self.root/'escaped';pidfile=self.root/'child.pid'
        child='import pathlib,time;time.sleep(.6);pathlib.Path('+repr(str(marker))+').write_text("escaped")'
        parent='import subprocess,sys,time,pathlib;p=subprocess.Popen([sys.executable,"-c",'+repr(child)+']);pathlib.Path('+repr(str(pidfile))+').write_text(str(p.pid));time.sleep(10)'
        with self.assertRaises(commands.CommandFailure) as caught:commands.run(sys.executable,'-c',parent,timeout=.2)
        self.assertEqual(caught.exception.record['classification'],'TIMEOUT')
        time.sleep(.65);self.assertFalse(marker.exists());self.assertTrue(pidfile.exists())
    def test_baseline_direct_child_timeout_leaves_descendant(self):
        # Safe counterexample: old subprocess.run(timeout=...) kills its direct
        # child but cannot prevent this short-lived descendant's delayed action.
        marker=self.root/'baseline-escaped';pidfile=self.root/'baseline.pid'
        child='import pathlib,time;time.sleep(.3);pathlib.Path('+repr(str(marker))+').write_text("escaped")'
        parent='import subprocess,sys,time,pathlib;p=subprocess.Popen([sys.executable,"-c",'+repr(child)+']);pathlib.Path('+repr(str(pidfile))+').write_text(str(p.pid));time.sleep(10)'
        try:
            with self.assertRaises(subprocess.TimeoutExpired):subprocess.run([sys.executable,'-c',parent],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=.1)
            time.sleep(.4);self.assertTrue(marker.exists())
        finally:
            if pidfile.exists():
                try:os.kill(int(pidfile.read_text()),signal.SIGKILL)
                except ProcessLookupError:pass
    def test_pipe_holding_descendant_cannot_defeat_deadline(self):
        parent='import subprocess,sys;subprocess.Popen([sys.executable,"-c","import time;time.sleep(10)"])'
        start=time.monotonic()
        with self.assertRaises(commands.CommandFailure):commands.run(sys.executable,'-c',parent,timeout=.15)
        self.assertLess(time.monotonic()-start,2)
    def test_unbounded_output_is_stopped_and_never_saved(self):
        with self.assertRaises(commands.CommandFailure) as caught:commands.run(sys.executable,'-c','import sys;sys.stderr.write("secret"*50000)',timeout=2)
        self.assertEqual(caught.exception.record['classification'],'OUTPUT_LIMIT')
        self.assertLess((self.root/'operation-linux.json').stat().st_size,1000)
    def test_signal_exit_is_not_success(self):
        with self.assertRaises(commands.CommandFailure) as caught:commands.run(sys.executable,'-c','import os,signal;os.kill(os.getpid(),signal.SIGTERM)')
        self.assertEqual(caught.exception.record['classification'],'SIGNAL');self.assertEqual(caught.exception.record['exitCode'],-signal.SIGTERM)
    def test_real_supervisor_signal_finalizes_command_receipt(self):
        code='import sys,pathlib;sys.path.insert(0,'+repr(str(Path(__file__).parent))+');import native_linux_commands as c;c.EVIDENCE=pathlib.Path('+repr(str(self.root))+');c.handle_signals();c.run(sys.executable,"-c","import time;time.sleep(10)")'
        proc=subprocess.Popen([sys.executable,'-B','-c',code],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            deadline=time.monotonic()+2
            while not (self.root/'operation-linux.json').exists() and time.monotonic()<deadline:time.sleep(.01)
            proc.send_signal(signal.SIGTERM);proc.wait(timeout=3)
            self.assertEqual(self.receipt()['classification'],'INTERRUPTED')
            self.assertEqual(self.receipt()['signal'],signal.SIGTERM)
            self.assertEqual(self.receipt()['state'],'FINISHED')
        finally:
            if proc.poll() is None:proc.kill();proc.wait()
    def test_atomic_progress_survives_next_operation(self):
        evidence=self.root/'evidence';evidence.mkdir();phases=native.Phases(self.root,'fixture')
        phases.append({'phase':'completed','status':'PASS'})
        with self.assertRaises(commands.CommandFailure):commands.run(sys.executable,'-c','raise SystemExit(2)')
        self.assertEqual(json.loads((evidence/'progress-linux.json').read_text())['completed'][0]['phase'],'completed')

class FailureDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        (self.root/'evidence').mkdir()
        self.valid={'pid':123,'port':4567,'count':2,'rejected':0,'firstReportAt':1000,'lastReportAt':61000}
    def state(self,value):
        (self.root/'receiver.json').write_text(json.dumps(value))
        return native.receiver_diagnostic(self.root)
    def test_valid_state_projects_only_counts_and_derived_interval(self):
        self.assertEqual(self.state(self.valid),{'state':'VALID','count':2,'rejected':0,'intervalMs':60000})
        self.assertEqual(self.state({**self.valid,'count':0,'firstReportAt':None,'lastReportAt':None}),{'state':'VALID','count':0,'rejected':0,'intervalMs':None})
    def test_missing_invalid_and_unverified_never_substitute_zero(self):
        self.assertEqual(native.receiver_diagnostic(self.root)['state'],'MISSING')
        for raw in ['', '{', 'secret'*1000, '[]']:
            (self.root/'receiver.json').write_text(raw)
            result=native.receiver_diagnostic(self.root)
            self.assertEqual(result['state'],'INVALID');self.assertIsNone(result['count']);self.assertIsNone(result['rejected'])
        with mock.patch.object(native.os,'open',side_effect=PermissionError('secret')):
            self.assertEqual(native.receiver_diagnostic(self.root),{'state':'UNVERIFIED','count':None,'rejected':None,'intervalMs':None})
    def test_invalid_fields_and_bounds(self):
        for key,value in [('count',True),('count',-1),('count',1000001),('rejected','secret'),('firstReportAt',float('nan')),('lastReportAt',999),('lastReportAt',86401001),('pid',0),('port',65536)]:
            with self.subTest(key=key,value=value):
                result=self.state({**self.valid,key:value});self.assertEqual(result['state'],'INVALID');self.assertIsNone(result['count'])
        self.assertEqual(self.state({**self.valid,'count':1})['state'],'INVALID')
        self.assertEqual(self.state({**self.valid,'secret':'do-not-emit'})['state'],'INVALID')
    def test_link_fifo_and_changed_file_cannot_be_valid(self):
        target=self.root/'secret';target.write_text(json.dumps(self.valid));link=self.root/'receiver.json';link.symlink_to(target)
        self.assertEqual(native.receiver_diagnostic(self.root)['state'],'UNVERIFIED');link.unlink()
        os.mkfifo(link);self.assertEqual(native.receiver_diagnostic(self.root)['state'],'INVALID');link.unlink()
        self.state(self.valid)
        before=os.stat(link);after=mock.Mock(st_size=before.st_size,st_mtime_ns=before.st_mtime_ns+1)
        with mock.patch.object(native.os,'fstat',side_effect=[before,after]):self.assertEqual(native.receiver_diagnostic(self.root)['state'],'UNVERIFIED')
    def test_owned_location_and_secret_exception_are_closed(self):
        filename=str(native.ROOT/'scripts/qualify-native-linux.py')
        namespace={};exec(compile('def fail():\n    assert False, "secret/path?token=hidden"\n',filename,'exec'),namespace)
        try:namespace['fail']()
        except AssertionError as exc:result=native.failure_diagnostic(exc,self.root)
        self.assertEqual(result['category'],'ASSERTION');self.assertEqual(result['locations'],[{'script':'qualify-native-linux.py','line':2}])
        self.assertNotIn('secret',json.dumps(result));self.assertNotIn(str(native.ROOT),json.dumps(result))
    def test_foreign_same_basename_and_long_or_nested_trace_are_bounded(self):
        for filename in ['/foreign/qualify-native-linux.py',str(native.ROOT/'scripts/../scripts/qualify-native-linux.py'),'<secret>']:
            namespace={};exec(compile('def fail():\n    raise KeyError("secret")\n',filename,'exec'),namespace)
            try:namespace['fail']()
            except KeyError as exc:result=native.failure_diagnostic(exc,self.root)
            self.assertEqual(result['locations'],[]);self.assertEqual(result['category'],'KEY')
        namespace={};exec(compile('def fail(n):\n    if n: return fail(n-1)\n    raise RuntimeError("secret")\n',str(native.ROOT/'scripts/qualify-native-linux.py'),'exec'),namespace)
        try:namespace['fail'](70)
        except RuntimeError as exc:result=native.failure_diagnostic(exc,self.root)
        self.assertLessEqual(len(result['locations']),2)
    def test_command_record_fields_are_whitelisted(self):
        record={'operation':'OWNED_SCOPE','classification':'EXIT_NONZERO','exitCode':1,'argv':'secret','diagnosticLabels':['secret']}
        result=native.failure_diagnostic(commands.CommandFailure(record),self.root)
        self.assertEqual(result['command'],{'operation':'OWNED_SCOPE','classification':'EXIT_NONZERO','exitCode':1});self.assertNotIn('secret',json.dumps(result))
        for code in [True,256,-65,'secret']:
            self.assertEqual(native.command_diagnostic({'operation':'secret','classification':'secret','exitCode':code}),{'operation':'UNCLASSIFIED','classification':'UNCLASSIFIED','exitCode':None})
    def test_hostile_exception_getters_and_diagnostic_errors_do_not_escape(self):
        class Hostile(RuntimeError):
            def __str__(self):raise AssertionError('secret')
            def __getattribute__(self,name):raise AssertionError('secret')
        self.assertEqual(native.failure_diagnostic(Hostile(),self.root)['category'],'OTHER')
        with mock.patch.object(native,'receiver_diagnostic',side_effect=RuntimeError('secret')):
            result=native.failure_diagnostic(AssertionError('primary-secret'),self.root)
        self.assertEqual(result['category'],'ASSERTION');self.assertEqual(result['receiver']['state'],'UNVERIFIED')
    def test_actual_qualifier_catch_preserves_primary_before_cleanup_overwrite(self):
        failure=AssertionError('primary-secret');self.state(self.valid)
        original={'operation':'NODE','classification':'EXIT_NONZERO','exitCode':2}
        commands.atomic(self.root/'evidence/operation-linux.json',original)
        (self.root/'evidence/prepared.json').write_text(json.dumps({'bundles':{'bundle':'a'*64},'head':'b'*40}))
        with mock.patch.object(native,'guard'),mock.patch.object(native,'require_output',return_value=self.root),mock.patch.object(native,'MANAGED',[]),mock.patch.object(native,'command',return_value=mock.Mock(stdout='not-found')),mock.patch.object(native.pwd,'getpwnam',side_effect=KeyError),mock.patch.object(native.grp,'getgrnam',side_effect=KeyError),mock.patch.dict(os.environ,{'GITHUB_RUN_ID':'12345'}),mock.patch.object(native,'fixed_ancestor_metadata',side_effect=failure):
            with self.assertRaises(AssertionError) as caught:native.qualify(self.root)
        self.assertIs(caught.exception,failure)
        saved=json.loads((self.root/'evidence/lifecycle-linux.json').read_text());snapshot=saved['diagnostic']
        self.assertEqual(snapshot['category'],'ASSERTION');self.assertEqual(snapshot['lastCommand'],original);self.assertEqual(snapshot['receiver']['intervalMs'],60000)
        commands.atomic(self.root/'evidence/operation-linux.json',{'operation':'SYSTEMCTL','classification':'SUCCESS','exitCode':0})
        saved['cleanup']={'status':'PASS'};commands.atomic(self.root/'evidence/lifecycle-linux.json',saved)
        self.assertEqual(json.loads((self.root/'evidence/lifecycle-linux.json').read_text())['diagnostic'],snapshot)
        self.assertNotIn('primary-secret',json.dumps(saved))
    def test_scope_exit_is_not_query_failure(self):
        failure=commands.CommandFailure({'operation':'OWNED_SCOPE','classification':'EXIT_NONZERO','exitCode':1})
        self.assertEqual(isolation.failure_record(failure,'QUALIFIER')['reason'],'OWNED_SCOPE_FAILED')
        failure.record['operation']='SYSTEMCTL'
        self.assertEqual(isolation.failure_record(failure,'CLEANUP')['reason'],'COMMAND_FAILED')
        self.assertEqual(isolation.failure_record(isolation.AdmissionRefused('QUERY_FAILED',query='LIST_UNITS'),'UNIT_ADMISSION')['reason'],'QUERY_FAILED')

class SliceTests(unittest.TestCase):
    def test_run_id_bounded_canonical_identity(self):
        for run in ['1','12345','99999999999999999999']:
            with mock.patch.dict(os.environ,{'GITHUB_RUN_ID':run}):
                self.assertEqual(isolation.names(),{'run':run,'slice':'vmpowerfixture'+run+'.slice','qualify':'vmpowerqualify'+run+'.scope','cleanup':'vmpowercleanup'+run+'.scope'})
        for run in ['', '0', '01', '1\n', '+1', '１', '1/2', '1;2', '1'*21, '1'*1000000]:
            with self.subTest(run=run[:30]),mock.patch.dict(os.environ,{'GITHUB_RUN_ID':run}),mock.patch.object(Path,'read_text',side_effect=AssertionError('filesystem reached')) as reads,mock.patch.object(isolation,'require_output',side_effect=AssertionError('output reached')) as output,mock.patch.object(commands,'run',side_effect=AssertionError('command reached')) as command,mock.patch.object(isolation.socket,'create_connection',side_effect=AssertionError('socket reached')) as connect,mock.patch.object(isolation,'guard'):
                with self.assertRaisesRegex(ValueError,'RUN_REFUSED'):isolation.names()
                with self.assertRaisesRegex(ValueError,'RUN_REFUSED'):isolation.probe(run,1)
                with self.assertRaisesRegex(ValueError,'RUN_REFUSED'):isolation.supervise(Path('/unused'))
                # Execute the exact independently installed hook, not a mock copy.
                with mock.patch.object(sys,'argv',['slice-probe.py',run,'1']),mock.patch.object(sys,'stderr',new_callable=io.StringIO) as stderr:
                    with self.assertRaises(SystemExit) as stopped:
                        exec(compile(isolation.probe_bytes(),'<installed-hook>','exec'),{'__name__':'__main__'})
                    self.assertEqual(stopped.exception.code,1)
                    self.assertEqual(stderr.getvalue(),'FIXTURE_ISOLATION_PROBE_REFUSED\n')
                for effect in [reads,output,command,connect]:effect.assert_not_called()

    def setUp(self):
        self.env=mock.patch.dict(os.environ,{'GITHUB_RUN_ID':'12345'});self.env.start();self.addCleanup(self.env.stop)
    def test_wrong_cgroup_and_descendant_prefix_rejected(self):
        for value in ['0::/system.slice/runner.service','0::/vmpowerfixture12345.slice-other/x','1::/vmpowerfixture12345.slice/x']:
            with self.subTest(value=value),mock.patch.object(Path,'read_text',return_value=value):
                with self.assertRaisesRegex(RuntimeError,'MEMBERSHIP'):isolation.member(1)
        with mock.patch.object(Path,'read_text',return_value='0::/vmpowerfixture12345.slice/vmpower.service'):
            self.assertEqual(isolation.member(1,'vmpower.service'),'/vmpowerfixture12345.slice/vmpower.service')
            with self.assertRaisesRegex(RuntimeError,'UNIT_REFUSED'):isolation.member(1,'other.service')
    def test_probe_fails_before_socket_if_membership_wrong(self):
        with mock.patch.object(Path,'read_text',return_value='0::/system.slice/runner.service'),mock.patch.object(isolation.socket,'socket',side_effect=AssertionError('no socket')):
            with self.assertRaisesRegex(RuntimeError,'MEMBERSHIP'):isolation.probe('12345',1)
    def test_probe_external_success_is_not_denial(self):
        connection=mock.MagicMock();connection.__enter__.return_value=connection
        with mock.patch.object(Path,'read_text',return_value='0::/vmpowerfixture12345.slice/vmpower.service'),mock.patch.object(isolation.socket,'create_connection'),mock.patch.object(isolation.socket,'socket',return_value=connection):
            with self.assertRaisesRegex(RuntimeError,'EXTERNAL_CONNECTED'):isolation.probe('12345',1)
    def test_both_socket_families_and_loopback_are_tested(self):
        connection=mock.MagicMock();connection.__enter__.return_value=connection;connection.connect.side_effect=PermissionError(errno.EPERM,'denied')
        with mock.patch.object(Path,'read_text',return_value='0::/vmpowerfixture12345.slice/vmpower.service'),mock.patch.object(isolation.socket,'create_connection') as positive,mock.patch.object(isolation.socket,'socket',return_value=connection) as sockets:
            result=isolation.probe('12345',1234)
        self.assertEqual(result['ipv4'],'EPERM');self.assertEqual(result['ipv6'],'EPERM');positive.assert_called_once_with(('127.0.0.1',1234),timeout=3)
        self.assertEqual([c.args[0] for c in sockets.call_args_list],[isolation.socket.AF_INET,isolation.socket.AF_INET6])
    def test_exact_installed_probe_includes_canonical_parser_and_runs_both_families(self):
        connection=mock.MagicMock();connection.__enter__.return_value=connection;connection.connect.side_effect=PermissionError(errno.EPERM,'denied')
        with mock.patch.object(Path,'read_text',return_value='0::/vmpowerfixture12345.slice/vmpower.service'),mock.patch.object(isolation.socket,'create_connection') as positive,mock.patch.object(isolation.socket,'socket',return_value=connection) as sockets,mock.patch.object(sys,'argv',['slice-probe.py','12345','1234']),mock.patch.object(sys,'stdout',new_callable=io.StringIO) as stdout:
            exec(compile(isolation.probe_bytes(),'<installed-hook>','exec'),{'__name__':'__main__'})
        self.assertEqual(json.loads(stdout.getvalue()),{'membership':'PASS','loopback':'PASS','ipv4':'EPERM','ipv6':'EPERM'})
        positive.assert_called_once_with(('127.0.0.1',1234),timeout=3)
        self.assertEqual([c.args[0] for c in sockets.call_args_list],[isolation.socket.AF_INET,isolation.socket.AF_INET6])
    def test_missing_kernel_support_prevents_root_probe(self):
        with mock.patch.object(isolation,'verify',side_effect=RuntimeError('FIXTURE_ISOLATION_BPF_UNSUPPORTED')),mock.patch.object(isolation,'probe',side_effect=AssertionError('no probe')):
            with self.assertRaisesRegex(RuntimeError,'BPF_UNSUPPORTED'):isolation.begin(None,Path('/fixture'),1)
    def test_actual_package_hook_guard_precedes_each_mutation(self):
        for operation in ['--install','--remove','--purge']:
            with mock.patch.object(isolation,'verify'),mock.patch.object(isolation,'journal',return_value={'port':1234}),mock.patch.object(Path,'read_bytes',return_value=isolation.probe_bytes()):
                args=isolation.package_args(Path('/fixture'),('dpkg',operation,'fixed.deb'))
                self.assertTrue(args[1].startswith('--pre-invoke=/usr/bin/python3 /opt/'))
                self.assertEqual(args[2:],(operation,'fixed.deb'))
    def test_changed_hook_refused_before_package_command(self):
        with mock.patch.object(isolation,'verify'),mock.patch.object(isolation,'journal',return_value={'port':1234}),mock.patch.object(Path,'read_bytes',return_value=b'changed'):
            with self.assertRaisesRegex(RuntimeError,'HOOK_GUARD'):isolation.package_args(Path('/fixture'),('dpkg','--purge','fixed'))
    def test_only_exact_owned_scope_can_be_stopped(self):
        with mock.patch.object(commands,'run',side_effect=AssertionError('no system call')):
            with self.assertRaisesRegex(RuntimeError,'SCOPE_REFUSED'):isolation.stop_scope('runner.service',Path('/unused'))
    def test_cleanup_scope_failure_reaps_prior_scope_and_finalizes_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);(out/'evidence').mkdir();(out/'owned.json').write_text('{}')
            (out/'isolation.json').write_text(json.dumps({'identity':isolation.names(),'state':'READY','unitsAbsentAtAdmission':True,'ownershipToken':'a'*32,'unitStates':{'slice':'CREATE_INTENT','qualify':'CREATE_INTENT','cleanup':'NOT_CREATED'}}))
            original_read=Path.read_text
            def read(target,*args,**kwargs):
                return '0::/system.slice/runner.service' if str(target)=='/proc/self/cgroup' else original_read(target,*args,**kwargs)
            events=[]
            def execute(*args,**kwargs):
                self.assertEqual(args[0],'systemd-run');events.append('cleanup-start')
                raise commands.CommandFailure({'operation':'OWNED_SCOPE','classification':'TIMEOUT'})
            with mock.patch.object(isolation,'guard'),mock.patch.object(isolation,'require_output',return_value=out),mock.patch.object(Path,'read_text',autospec=True,side_effect=read),mock.patch.object(isolation,'stop_scope',side_effect=lambda name,out:events.append(name)),mock.patch.object(commands,'run',side_effect=execute),mock.patch.object(commands,'EVIDENCE'),mock.patch.object(commands,'OPERATION_FILE'):
                with self.assertRaises(commands.CommandFailure):isolation.supervise(out,cleanup_only=True)
            self.assertEqual(events,['vmpowerqualify12345.scope','cleanup-start','vmpowercleanup12345.scope'])
            self.assertEqual(json.loads((out/'evidence/cleanup-linux.json').read_text())['status'],'FAIL')
            self.assertEqual(json.loads((out/'evidence/isolation-linux.json').read_text())['status'],'FAIL')
    def test_service_descendants_must_be_gone_before_purge(self):
        fake=mock.MagicMock();fake.read_text.return_value='42'
        with mock.patch.object(Path,'exists',return_value=True),mock.patch.object(Path,'rglob',return_value=[fake]):
            with self.assertRaisesRegex(RuntimeError,'DESCENDANTS_REMAIN'):isolation.require_empty('vmpower.service')
    def test_effective_kernel_filter_query_uses_ingress_and_egress(self):
        queried=[]
        def query(number,operation,pointer,size):
            queried.append((number,operation,pointer._obj.attach_type,pointer._obj.query_flags,size))
            pointer._obj.prog_cnt=1;return 0
        with mock.patch.object(isolation,'guard'),mock.patch.object(isolation.os,'open',return_value=9),mock.patch.object(isolation.os,'close'),mock.patch.object(isolation.ctypes,'CDLL',return_value=mock.Mock(syscall=query)):
            self.assertEqual(isolation.effective_filters('/vmpowerfixture12345.slice/test.scope'),{'ingressPrograms':1,'egressPrograms':1})
        self.assertEqual([(n,op,kind,flag) for n,op,kind,flag,_ in queried],[(321,16,0,1),(321,16,1,1)])
    def test_each_existing_unit_or_cgroup_refused_before_setup_or_stop(self):
        for collision in ('slice','qualify','cleanup'):
            for kind in ('loaded-unit','cgroup'):
                with self.subTest(collision=collision,kind=kind),tempfile.TemporaryDirectory() as directory:
                    out=Path(directory);(out/'evidence').mkdir();drop=out/'drop';calls=[]
                    original_read=Path.read_text
                    def read(target,*args,**kwargs):
                        return '0::/system.slice/runner.service' if str(target)=='/proc/self/cgroup' else original_read(target,*args,**kwargs)
                    def execute(*args,**kwargs):
                        calls.append(args)
                        if args[:2]==('systemctl','list-units'):
                            return mock.Mock(stdout=(isolation.names()[collision]+' loaded inactive dead fixture') if kind=='loaded-unit' else '')
                        if args[:2]==('systemctl','list-unit-files'):return mock.Mock(stdout='')
                        if args==('systemctl','show','--property=UnitPath','--value'):return mock.Mock(stdout=str(out/'units'))
                        self.assertEqual(args[2],'vmpower.service');return mock.Mock(stdout='not-found')
                    def group(key):return out/('cgroup-'+key)
                    if kind=='cgroup':group(collision).mkdir()
                    with mock.patch.object(isolation,'guard'),mock.patch.object(isolation,'require_output',return_value=out),mock.patch.object(isolation,'DROP',drop),mock.patch.object(isolation,'policy',return_value={}),mock.patch.object(isolation,'unit_group',side_effect=group),mock.patch.object(Path,'read_text',autospec=True,side_effect=read),mock.patch.object(commands,'run',side_effect=execute),mock.patch.object(commands,'EVIDENCE'),mock.patch.object(commands,'OPERATION_FILE'),mock.patch.object(isolation,'stop_scope') as stopped:
                        with self.assertRaisesRegex(RuntimeError,'UNIT_COLLISION'):isolation.supervise(out)
                    self.assertFalse((out/'isolation.json').exists());self.assertFalse(drop.exists());stopped.assert_not_called()
                    failure=json.loads((out/'evidence/supervisor-outcome-linux.json').read_text())['failure']
                    self.assertEqual(failure,{'phase':'UNIT_ADMISSION','resource':collision,'reason':'LOADED_UNIT' if kind=='loaded-unit' else 'CGROUP'})
    def test_absent_implicit_slice_is_not_loaded_by_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);calls=[]
            def execute(*args,**kwargs):
                calls.append(args)
                if args[1] in ('list-units','list-unit-files'):return mock.Mock(stdout='')
                if args==('systemctl','show','--property=UnitPath','--value'):return mock.Mock(stdout=str(root/'units'))
                # systemd255 loads optional-fragment slices during named GetAll.
                return mock.Mock(stdout='loaded')
            with mock.patch.object(commands,'run',side_effect=execute),mock.patch.object(isolation,'unit_group',side_effect=lambda key:root/key):isolation.admit_units()
            self.assertEqual(len(calls),2);self.assertIn('--all',calls[0])
            self.assertEqual(calls[-1],('systemctl','show','--property=UnitPath','--value'))
    def test_unloaded_files_dropins_and_dangling_masks_are_collisions(self):
        for key in ('slice','qualify','cleanup'):
            for kind in ('file','dropin','dangling-mask'):
                with self.subTest(key=key,kind=kind),tempfile.TemporaryDirectory() as directory:
                    root=Path(directory);units=root/'units';units.mkdir();target=units/isolation.names()[key]
                    if kind=='file':target.write_text('foreign')
                    elif kind=='dropin':Path(str(target)+'.d').mkdir()
                    else:target.symlink_to(root/'absent')
                    def execute(*args,**kwargs):return mock.Mock(stdout=str(units) if args[1]=='show' else '')
                    with mock.patch.object(commands,'run',side_effect=execute),mock.patch.object(isolation,'unit_group',side_effect=lambda key:root/('cg-'+key)):
                        with self.assertRaises(isolation.AdmissionRefused) as caught:isolation.admit_units()
                    self.assertEqual(caught.exception.reason,'DROPIN' if kind=='dropin' else 'UNIT_FILE');self.assertEqual(caught.exception.resource,key)
    def test_memory_and_installed_enumeration_refuse_any_exact_unit(self):
        for verb in ('list-units',):
            for state in ('loaded inactive dead','not-found inactive dead','loaded active running transient'):
                with self.subTest(verb=verb,state=state):
                    def execute(*args,**kwargs):return mock.Mock(stdout=isolation.names()['slice']+' '+state if args[1]==verb else '')
                    with mock.patch.object(commands,'run',side_effect=execute):
                        with self.assertRaises(isolation.AdmissionRefused) as caught:isolation.admit_units()
                    self.assertEqual(caught.exception.reason,'LOADED_UNIT')
    def test_empty_unit_file_match_exit_one_never_becomes_an_admission_query(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);calls=[]
            def execute(*args,**kwargs):
                calls.append(args)
                if args[1]=='list-unit-files':raise commands.CommandFailure({'operation':'SYSTEMCTL','classification':'EXIT_NONZERO','exitCode':1})
                return mock.Mock(stdout=str(root/'units') if args[1]=='show' else '')
            with mock.patch.object(commands,'run',side_effect=execute),mock.patch.object(isolation,'unit_group',side_effect=lambda key:root/key):isolation.admit_units()
            self.assertFalse(any(args[1]=='list-unit-files' for args in calls))
    def test_each_query_failure_has_a_closed_query_tag(self):
        for tag in ('LIST_UNITS','MANAGER_UNIT_PATH'):
            with self.subTest(tag=tag),tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                def execute(*args,**kwargs):
                    current='LIST_UNITS' if args[1]=='list-units' else 'MANAGER_UNIT_PATH'
                    if current==tag:raise commands.CommandFailure({'operation':'SYSTEMCTL','classification':'EXIT_NONZERO','exitCode':1})
                    return mock.Mock(stdout='')
                with mock.patch.object(commands,'run',side_effect=execute):
                    with self.assertRaises(isolation.AdmissionRefused) as caught:isolation.admit_units()
                self.assertEqual(isolation.failure_record(caught.exception,'UNIT_ADMISSION'),{'phase':'UNIT_ADMISSION','resource':'local','reason':'QUERY_FAILED','query':tag})
    def test_malformed_queries_and_unreadable_metadata_fail_closed(self):
        for raw in ('','relative/path','/safe/../other','/safe\nunsafe','/safe;unexpected'):
            with self.subTest(raw=raw):
                def execute(*args,**kwargs):return mock.Mock(stdout=raw if args[1]=='show' else '')
                with mock.patch.object(commands,'run',side_effect=execute):
                    with self.assertRaises(isolation.AdmissionRefused) as caught:isolation.admit_units()
                self.assertEqual(caught.exception.reason,'PARSE_FAILED')
        with mock.patch.object(commands,'run',return_value=mock.Mock(stdout='unexpected sensitive text')):
            with self.assertRaises(isolation.AdmissionRefused) as caught:isolation.admit_units()
        self.assertEqual(caught.exception.reason,'PARSE_FAILED');self.assertNotIn('sensitive',str(caught.exception))
        failure=commands.CommandFailure({'operation':'SYSTEMCTL','classification':'EXIT_NONZERO'})
        with mock.patch.object(commands,'run',side_effect=failure):
            with self.assertRaises(isolation.AdmissionRefused) as caught:isolation.admit_units()
        self.assertEqual(caught.exception.reason,'QUERY_FAILED');self.assertEqual(caught.exception.query,'LIST_UNITS')
        with mock.patch.object(Path,'lstat',side_effect=PermissionError('withheld')):
            with self.assertRaises(PermissionError):isolation.occupied(Path('/unreadable'))
    def test_exclusive_policy_creation_preserves_raced_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'unit';target.write_bytes(b'foreign')
            with self.assertRaises(FileExistsError):isolation.create_policy(target,b'fixture')
            self.assertEqual(target.read_bytes(),b'foreign')
    def test_preflight_failure_survives_no_journal_cleanup_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);(out/'evidence').mkdir();system=out/'system';system.mkdir()
            original_read=Path.read_text
            def read(target,*args,**kwargs):return '0::/system.slice/runner.service' if str(target)=='/proc/self/cgroup' else original_read(target,*args,**kwargs)
            with mock.patch.object(isolation,'guard'),mock.patch.object(isolation,'require_output',return_value=out),mock.patch.object(isolation,'SYSTEM',system),mock.patch.object(isolation,'DROP',system/'vmpower.service.d'),mock.patch.object(Path,'read_text',autospec=True,side_effect=read),mock.patch.object(isolation,'admit_units',side_effect=isolation.AdmissionRefused('LOADED_UNIT','slice')),mock.patch.object(commands,'run',return_value=mock.Mock(stdout='not-found')):
                with self.assertRaises(isolation.AdmissionRefused):isolation.supervise(out)
                receipt=(out/'evidence/supervisor-outcome-linux.json').read_bytes()
                self.assertEqual(json.loads(receipt)['failure'],{'phase':'UNIT_ADMISSION','resource':'slice','reason':'LOADED_UNIT'})
                with self.assertRaises(isolation.AdmissionRefused) as caught:isolation.supervise(out,cleanup_only=True)
                self.assertEqual(caught.exception.reason,'NO_JOURNAL');self.assertEqual((out/'evidence/supervisor-outcome-linux.json').read_bytes(),receipt)
                self.assertEqual(json.loads((out/'evidence/cleanup-linux.json').read_text())['status'],'FAIL')
    def test_unowned_loaded_scope_cannot_be_stopped_after_admission(self):
        data={'identity':isolation.names(),'ownershipToken':'a'*32,'unitStates':{'qualify':'CREATE_INTENT'}}
        with mock.patch.object(isolation,'journal',return_value=data),mock.patch.object(commands,'run',return_value=mock.Mock(stdout='LoadState=loaded\nDescription=foreign\nControlGroup=/vmpowerfixture12345.slice/vmpowerqualify12345.scope')) as execute:
            with self.assertRaisesRegex(RuntimeError,'OWNERSHIP_REFUSED'):isolation.stop_scope(isolation.names()['qualify'],Path('/unused'))
        self.assertEqual(len(execute.call_args_list),1);self.assertEqual(execute.call_args.args[:2],('systemctl','show'))
    def test_matched_created_scope_is_journaled_before_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)
            data={'identity':isolation.names(),'state':'READY','unitsAbsentAtAdmission':True,'ownershipToken':'a'*32,'unitStates':{'slice':'CREATE_INTENT','qualify':'CREATE_INTENT','cleanup':'NOT_CREATED'}}
            (out/'isolation.json').write_text(json.dumps(data));calls=[]
            def execute(*args,**kwargs):
                calls.append(args)
                if args[1]=='show':return mock.Mock(stdout='LoadState=loaded\nDescription='+isolation.description(data,'qualify')+'\nControlGroup=/vmpowerfixture12345.slice/vmpowerqualify12345.scope')
                self.assertEqual(json.loads((out/'isolation.json').read_text())['unitStates']['qualify'],'OWNED')
                return mock.Mock(stdout='')
            with mock.patch.object(commands,'run',side_effect=execute):isolation.stop_scope(isolation.names()['qualify'],out)
            self.assertEqual([args[1] for args in calls],['show','stop'])
            self.assertEqual(json.loads((out/'isolation.json').read_text())['unitStates']['qualify'],'STOPPED')
    def test_unadmitted_journal_cannot_authorize_a_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);(out/'isolation.json').write_text(json.dumps({'identity':isolation.names(),'state':'READY'}))
            with mock.patch.object(commands,'run',side_effect=AssertionError('no command')):
                with self.assertRaisesRegex(RuntimeError,'JOURNAL_REFUSED'):isolation.stop_scope(isolation.names()['qualify'],out)
    def test_qualifier_timeout_or_signal_survives_successful_cleanup(self):
        failures=[commands.CommandFailure({'operation':'OWNED_SCOPE','classification':'TIMEOUT','diagnosticLabels':['UNCLASSIFIED']}),commands.Interrupted(signal.SIGTERM)]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__),tempfile.TemporaryDirectory() as directory:
                out=Path(directory);(out/'evidence').mkdir();system=out/'system';system.mkdir()
                original_read=Path.read_text
                def read(target,*args,**kwargs):
                    return '0::/system.slice/runner.service' if str(target)=='/proc/self/cgroup' else original_read(target,*args,**kwargs)
                def execute(*args,**kwargs):
                    if args[0]=='systemd-run':
                        if '--cleanup' not in args:
                            (out/'owned.json').write_text('{}');raise failure
                        self.assertEqual(json.loads((out/'evidence/qualifier-outcome-linux.json').read_text())['status'],'FAIL')
                        commands.atomic(out/'evidence/operation-supervisor-linux.json',{'classification':'SUCCESS'})
                        commands.atomic(out/'evidence/cleanup-linux.json',{'status':'PASS'})
                    return mock.Mock(stdout='not-found')
                with mock.patch.object(isolation,'guard'),mock.patch.object(isolation,'require_output',return_value=out),mock.patch.object(isolation,'SYSTEM',system),mock.patch.object(isolation,'DROP',system/'vmpower.service.d'),mock.patch.object(isolation,'admit_units'),mock.patch.object(Path,'read_text',autospec=True,side_effect=read),mock.patch.object(isolation,'stop_scope'),mock.patch.object(isolation,'stop_owned'),mock.patch.object(commands,'run',side_effect=execute),mock.patch.object(commands,'EVIDENCE'),mock.patch.object(commands,'OPERATION_FILE'):
                    with self.assertRaises(type(failure)):isolation.supervise(out)
                saved=json.loads((out/'evidence/qualifier-outcome-linux.json').read_text())
                self.assertEqual(saved['status'],'FAIL')
                self.assertEqual(saved['failure']['classification'],'TIMEOUT' if isinstance(failure,commands.CommandFailure) else 'SIGNAL')
                final=json.loads((out/'evidence/isolation-linux.json').read_text())
                self.assertEqual(final['status'],'PASS');self.assertEqual(final['qualifierOutcome'],saved)
                self.assertEqual(json.loads((out/'evidence/cleanup-linux.json').read_text())['status'],'PASS')
    def test_purge_failure_always_writes_closed_cleanup_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);(out/'evidence').mkdir();(out/'owned.json').write_text(json.dumps({'run':'12345','paths':[str(p) for p in native.MANAGED]}))
            failure=commands.CommandFailure({'operation':'DPKG_PURGE','classification':'EXIT_NONZERO','exitCode':2,'diagnosticLabels':['DPKG_LOCK_HELD']})
            with mock.patch.object(native,'guard'),mock.patch.object(native,'require_output',return_value=out),mock.patch.object(native,'cleanup_owned',side_effect=failure):
                with self.assertRaises(commands.CommandFailure):native.cleanup(out)
            saved=json.loads((out/'evidence/cleanup-linux.json').read_text())
            self.assertEqual(saved['status'],'FAIL');self.assertEqual(saved['resourceAbsence'],'Unknown');self.assertEqual(saved['failure']['diagnosticLabels'],['DPKG_LOCK_HELD'])
    def test_slice_and_real_service_share_one_policy(self):
        entries=isolation.policy(1234)
        self.assertEqual(len(entries),2)
        self.assertIn(b'IPAddressDeny=any',next(raw for target,raw in entries.items() if target.suffix=='.slice'))
        drop=entries[isolation.DROP/'90-vmpower-fixture.conf']
        self.assertIn(b'Slice=vmpowerfixture12345.slice',drop);self.assertIn(b'ExecStartPre=',drop)
    def test_linux_workflow_has_no_staged_cli_before_supervisor(self):
        workflow=(Path(__file__).parents[1]/'.github/workflows/native-qualification.yml').read_text().split('  windows-native:')[0]
        self.assertNotIn('bundle/runtime/node',workflow)
        self.assertIn('linux-package-quarantine.py --output',workflow)
        self.assertIn('linux-package-quarantine.py --cleanup',workflow)

if __name__=='__main__':unittest.main()
