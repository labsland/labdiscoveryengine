"""Coordinator failure boundaries against disposable real Redis and HTTP peer model.

The peer models receipts only. CDC/physical behavior is verified separately.
"""
import json
from pathlib import Path
import time
from unittest.mock import patch

import pytest
import redis

from tests import test_maintenance_redis as maintenance_tests
from labdiscoveryengine.scheduling.serial_recovery import Coordinator, canonical, read_pending_journals


@pytest.fixture
def rig(tmp_path):
    host = maintenance_tests.MaintenanceRedis(); host.setUp()
    backend = redis.Redis(connection_pool=redis.ConnectionPool(
        host='127.0.0.1', port=host.r.connection_pool.connection_kwargs['port'], db=1,
        decode_responses=True, socket_timeout=1))
    target = dict(resource='board-1', backend_prefixes=['board-1:', 'nios-1:'],
                  controller_url='http://controller/instances/1',
                  readiness_url='http://backend/status', by_id='/dev/serial/by-id/usb-EXACT-if00')
    peer = Peer(target)
    host.r.set('lde:running','true')
    host.r.set('lde:serial-recovery-resources', 'board-1')
    host.r.set('lde:serial-recovery-protocol', 'cdc-v1:' + host.r.info('server')['run_id'])
    c = Coordinator(host.r, backend, tmp_path, request=peer.request, sleep=lambda _: None)
    try: yield c, peer, host, backend, target, tmp_path
    finally: backend.close(); host.tearDown()


class Peer:
    def __init__(self, target):
        self.target, self.generation = target, 'c'*32
        self.receipt, self.commands = {}, []
        self.busy, self.deleted = False, True
        self.hook = lambda action: None
        self.ready = True
    def request(self, url, data=None):
        if data:
            self.commands.append(data.copy())
            identity = dict(owner=data['owner'], generation=data['generation'], resource=self.target['resource'])
            if data['action']=='reconnect':
                self.busy, self.deleted = True, False
                self.receipt = dict(identity,phase='verified',samples=[1,2,3])
            else:
                self.busy=False
                self.receipt=dict(identity,phase='released',samples=[1,2,3])
            self.hook(data['action'])
            return dict(queued=True)
        if url.endswith('/serial-recovery'):
            return dict(receipt=self.receipt.copy(),status=dict(
                resource=self.target['resource'],by_id=self.target['by_id'],
                generation=self.generation,deleted_cdc=self.deleted,identity_present=True,
                busy=self.busy,at=time.time()))
        if url == self.target['readiness_url']: return dict(success=self.ready)
        return dict(success=True,pending_control_tasks=0,
                    pico_protocol=dict(success=True,checked_at=time.time()))


def journal(rig):return json.loads((rig[-1]/'board-1.json').read_text())
def held(rig):return rig[2].r.get('lde:resources:board-1:maintenance')


def test_success_releases_only_after_worker_and_backend_readiness(rig):
    c,p,h,b,t,d=rig
    assert c.run(t)=='recovered'
    assert [v['action'] for v in p.commands]==['reconnect','release']
    assert journal(rig)['phase']=='closed' and not held(rig)
    assert b.get('serial-recovery::board-1::owner') is None
    assert read_pending_journals(d)==[]


@pytest.mark.parametrize('busy',['assigned','queue','backend','session','pending','other-programmer'])
def test_preexisting_occupancy_preserved_without_dispatch(rig,busy):
    c,p,h,b,t,d=rig
    if busy=='assigned':h.r.set('lde:resources:board-1:assigned','student')
    if busy=='queue':h.queue()
    if busy=='other-programmer':b.set('nios-1:active-user','student')
    if busy=='backend':b.set('board-1:active-user','student')
    if busy=='session':b.set('board-1:weblab:sessions:student','live')
    if busy=='pending':b.sadd('board-1:weblab:index:v1:pending_tasks','task')
    before={k:b.dump(k) for k in b.keys('*')}
    assert c.run(t)=='busy' and not p.commands and not held(rig)
    assert before=={k:b.dump(k) for k in b.keys('*')}
    if busy=='queue': assert h.r.lrange(h.base+':queues:1',0,-1)==['student']


@pytest.mark.parametrize('action',['reconnect','release'])
def test_lost_http_reply_retains_hold_and_never_automatically_replays(rig,action):
    c,p,h,b,t,d=rig
    def lose_reply(actual):
        if actual==action:raise TimeoutError('response lost after execution')
    p.hook=lose_reply
    with pytest.raises(TimeoutError):c.run(t)
    assert held(rig) and len(read_pending_journals(d))==1
    commands=list(p.commands)
    assert c.run(t)=='reconciliation-required' and p.commands==commands
    p.hook=lambda _:None
    assert c.reconcile(t)=='reconciled' and not held(rig)
    assert len([v for v in p.commands if v['action']=='reconnect'])==1


def test_queue_arriving_after_acquire_waits_and_is_never_removed(rig):
    c,p,h,b,t,d=rig
    def arrive(action):
        if action=='reconnect':
            h.queue(); assert h.assign() is None
    p.hook=arrive
    assert c.run(t)=='recovered'
    assert h.r.lrange(h.base+':queues:1',0,-1)==['student']
    assert h.assign()=='student'


def test_failed_readiness_retains_hold_with_explicit_reconciliation(rig):
    c,p,h,b,t,d=rig;p.ready=False
    with pytest.raises(RuntimeError,match='Backend readiness'):c.run(t)
    assert held(rig) and journal(rig)['phase']=='worker-released'
    p.ready=True
    assert c.reconcile(t)=='reconciled'


def test_restarted_worker_cannot_supply_old_receipt_to_release(rig):
    c,p,h,b,t,d=rig
    def crash(action):
        if action=='reconnect':raise TimeoutError()
    p.hook=crash
    with pytest.raises(TimeoutError):c.run(t)
    p.generation='d'*32
    with pytest.raises(RuntimeError,match='restarted'):c.reconcile(t)
    assert held(rig) and len(p.commands)==1


def test_foreign_mirror_is_not_overwritten_or_released(rig):
    c,p,h,b,t,d=rig;b.set('serial-recovery::board-1::owner','foreign')
    with pytest.raises(RuntimeError,match='Existing controller'):c.run(t)
    assert held(rig) and not p.commands
    assert b.get('serial-recovery::board-1::owner')=='foreign'


def test_lost_maintenance_or_foreign_assignment_aborts_before_release(rig):
    c,p,h,b,t,d=rig
    def change(action):
        if action=='reconnect':h.r.set(h.base+':assigned','foreign')
    p.hook=change
    with pytest.raises(RuntimeError,match='Ownership'):c.run(t)
    assert held(rig) and h.r.get(h.base+':assigned')=='foreign'
    assert len(p.commands)==1


def test_missing_scheduler_capability_prevents_any_operation(rig):
    c,p,h,b,t,d=rig;h.r.delete('lde:serial-recovery-protocol')
    with pytest.raises(RuntimeError,match='not activated'):c.run(t)
    assert not p.commands and not list(d.glob('*.json'))


def test_disk_intent_survives_redis_flush(rig):
    c,p,h,b,t,d=rig
    def lose_state(action):
        if action=='reconnect':h.r.flushdb()
    p.hook=lose_state
    with pytest.raises(RuntimeError,match='Ownership'):c.run(t)
    pending=read_pending_journals(d)
    assert len(pending)==1 and pending[0]['phase']=='dispatching'
    assert len(p.commands)==1
