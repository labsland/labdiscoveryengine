import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from tests.test_lab_recovery import pkg
from recovery_under_test.gpio_readback import verify, wait_worker
from recovery_under_test.health import ReadinessCache
from recovery_under_test.managed import ManagedAdapter


class BaselineReadback(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        config = self.root/'gpio.yml'
        config.write_text("'1':\n  outputs:\n    button: {gpio_num: 2, active_low: true}\n    switch: {gpio_num: 3}\n")
        for number, value in [(2,'1'),(3,'0')]:
            pin=self.root/('gpio%d' % number);pin.mkdir()
            (pin/'direction').write_text('out');(pin/'value').write_text(value)
        self.plan=dict(protected_files={str(config):hashlib.sha256(config.read_bytes()).hexdigest()},
            gpio_readback=dict(configuration=str(config),instance='1',heartbeat_key='alive',
                               task_queues=['tasks'],required_stopped=['keyboard']))
        self.clock=0
        self.connection=Mock(); self.connection.llen.return_value=0
        self.connection.get.side_effect=lambda key:str(self.clock)
        self.identity=Mock(return_value=dict(programs={'keyboard':dict(state='STOPPED')}))
    def wait(self, seconds):self.clock += seconds
    def check(self):
        return verify(self.plan,'epoch',self.identity,self.connection,Mock(),
                      sleep=self.wait,clock=lambda:self.clock,gpio_root=self.root)
    def test_active_low_outputs_and_fresh_worker_iterations(self):
        result=self.check()
        self.assertEqual(result['outputs'],dict(button=True,switch=False))
        self.assertEqual(result['heartbeat_samples'],2)
    def test_cached_heartbeat_is_not_reset_execution_proof(self):
        self.connection.get.side_effect=None;self.connection.get.return_value='old'
        with self.assertRaises(RuntimeError):self.check()
    def test_incorrect_output_never_releases(self):
        (self.root/'gpio2/value').write_text('0')
        with self.assertRaises(RuntimeError):self.check()
    def test_pending_queue_never_releases(self):
        self.connection.llen.return_value=1
        with self.assertRaises(RuntimeError):self.check()
    def test_running_virtual_peripheral_never_releases(self):
        self.identity.return_value['programs']['keyboard']['state']='RUNNING'
        with self.assertRaises(RuntimeError):self.check()
    def test_controller_restart_during_readback_never_releases(self):
        self.identity.side_effect=[dict(programs={'keyboard':dict(state='STOPPED',pid=0)}),
                                   dict(programs={'keyboard':dict(state='STOPPED',pid=1)})]
        with self.assertRaises(RuntimeError):self.check()

    def test_slow_controller_initialization_precedes_physical_baseline(self):
        self.connection.get.side_effect=lambda key:None if self.clock<55 else str(self.clock)
        result=wait_worker(self.plan,'epoch',self.identity,self.connection,Mock(),sleep=self.wait,clock=lambda:self.clock)
        self.assertEqual(result['heartbeat_samples'],2)
        self.assertGreaterEqual(self.clock,55)
        self.assertLess(self.clock,120)

    def test_process_alive_without_task_loop_times_out(self):
        self.connection.get.side_effect=None;self.connection.get.return_value=None
        with self.assertRaisesRegex(RuntimeError,'did not finish initialization'):
            wait_worker(self.plan,'epoch',self.identity,self.connection,Mock(),sleep=self.wait,clock=lambda:self.clock)


class RestoreOrdering(unittest.TestCase):
    def test_transient_inspection_failure_is_retried_without_mutation(self):
        from recovery_under_test import managed
        adapter=ManagedAdapter(dict(stores={'scheduler':'redis://localhost/0'},resources={}))
        with patch.object(managed,'execute',side_effect=[RuntimeError('temporary'),'{"stable":true}']) as execute, patch.object(managed.time,'sleep'):
            self.assertEqual(adapter.boundary({'command':['agent']},'inspect'),{'stable':True})
        self.assertEqual(execute.call_count,2)

    def test_read_only_inspection_does_not_take_mutation_lock(self):
        import sys
        from recovery_under_test import agent
        with patch.object(sys,'argv',['agent','--plan','/plan','inspect']), patch.object(agent,'load',return_value={}), patch.object(agent,'identity',return_value={}), patch.object(agent,'exclusive',side_effect=AssertionError('read took lock')), patch('builtins.print'):
            agent.main()

    def test_no_programming_before_controller_ready(self):
        resource=dict(execution_boundaries=['runner','controller'],controller_boundary='controller',restore_boundary='baseline')
        adapter=ManagedAdapter(dict(stores={'scheduler':'redis://localhost/0'},resources={'board':resource}))
        calls=[]
        def boundary(config,action,epoch):
            calls.append((config,action))
            if action=='wait-controller':raise RuntimeError('initializing')
        adapter.boundary=boundary
        with self.assertRaises(RuntimeError):adapter.restore('board','epoch')
        self.assertEqual(calls,[('runner','start'),('controller','start'),('controller','wait-controller')])


class Readiness(unittest.TestCase):
    def test_requests_use_bounded_cache_and_expired_result_is_closed(self):
        now=[10]; probe=Mock(return_value=dict(service_ready=True))
        cache=ReadinessCache(probe,clock=lambda:now[0])
        self.assertFalse(cache.get()['service_ready']);probe.assert_not_called()
        cache.refresh()
        for _ in range(100):self.assertTrue(cache.get()['service_ready'])
        probe.assert_called_once()
        now[0]+=16;self.assertFalse(cache.get()['service_ready'])
    def test_failed_refresh_does_not_preserve_previous_success(self):
        probe=Mock(side_effect=[dict(service_ready=True),OSError('offline')])
        cache=ReadinessCache(probe);cache.refresh();cache.refresh()
        self.assertFalse(cache.get()['service_ready'])


class ResourceIdentity(unittest.TestCase):
    def test_normal_on_demand_peripheral_activity_is_not_executor_restart(self):
        plan=dict(stores={'scheduler':'redis://localhost/0'},resources={'board':
             dict(boundaries=[dict(required_running=['worker'])])})
        adapter=ManagedAdapter(plan);adapter.scheduler=Mock();adapter.scheduler.get.return_value=None
        row=dict(boot='boot',stores={},programs={'worker':dict(state='RUNNING',pid=10),
                                                'keyboard':dict(state='STOPPED',pid=0)})
        adapter.boundary=Mock(side_effect=lambda *a:copy.deepcopy(row))
        before=adapter.ready_identity('board')
        row['programs']['keyboard']=dict(state='RUNNING',pid=11)
        self.assertEqual(before,adapter.ready_identity('board'))
        row['programs']['worker']['pid']=12
        self.assertNotEqual(before,adapter.ready_identity('board'))

class ConfigurationFailure(unittest.TestCase):
    def test_configuration_failure_revokes_previously_ready_receipt(self):
        from recovery_under_test.managed import close_admission
        from recovery_under_test.state import save,read
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'state.json'
            save(path,dict(phase='managed',resources={'board':dict(phase='ready',epoch='old')},generation={}))
            # The filesystem trust check is qualified separately on Linux/root;
            # this portable test verifies the persisted failure transition.
            with patch('recovery_under_test.managed.validate_trusted_path'):
                close_admission(directory)
            state=read(path)
            self.assertEqual(state['phase'],'fencing')
            self.assertEqual(state['resources'],{})
            self.assertIsNone(state['generation'])

class PartialSchedulerStartup(unittest.TestCase):
    def test_partial_start_is_fenced_before_reapproval_and_retry(self):
        adapter=ManagedAdapter(dict(stores={'scheduler':'redis://localhost:6380/0'},resources={},
            storage_approve_command=['qualified-approval'],scheduler_boundary={'command':['qualified-agent']}))
        adapter.scheduler_identity=Mock(return_value={'programs':{'web':{'state':'RUNNING'},'worker':{'state':'FATAL'}}})
        order=[]
        adapter.fence_scheduler=Mock(side_effect=lambda epoch:order.append('fence'))
        adapter.boundary=Mock(side_effect=lambda *args:order.append('start') or {'ready':True})
        with patch('recovery_under_test.managed.execute',side_effect=lambda args:order.append('approve')):
            self.assertEqual(adapter.start_scheduler('epoch'),{'ready':True})
        self.assertEqual(order,['fence','approve','start'])
