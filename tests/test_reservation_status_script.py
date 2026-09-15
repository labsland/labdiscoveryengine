import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import redis


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "labdiscoveryengine" / "lua" / "get_reservation_status.lua"
REDIS_SERVER = "/opt/homebrew/bin/redis-server"


def _find_free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ReservationStatusLuaScriptTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls.port = _find_free_port()
        cls.process = subprocess.Popen(
            [
                REDIS_SERVER,
                '--bind', '127.0.0.1',
                "--port",
                str(cls.port),
                "--save",
                "",
                "--appendonly",
                "no",
                "--dir",
                cls._tmpdir.name,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        cls.redis = redis.Redis(host="127.0.0.1", port=cls.port, decode_responses=True)
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                if cls.redis.ping():
                    break
            except redis.RedisError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Temporary redis-server did not start for test_reservation_status_script")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "redis"):
            try:
                cls.redis.close()
            except Exception:
                pass
        if hasattr(cls, "process"):
            cls.process.terminate()
            cls.process.wait(timeout=10)
        if hasattr(cls, "_tmpdir"):
            cls._tmpdir.cleanup()

    def setUp(self):
        self.redis.flushdb()
        self.script = self.redis.register_script(SCRIPT_PATH.read_text(encoding="utf-8"))

    def test_pending_status_is_preserved_when_reservation_leaves_queue_before_hash_updates(self):
        reservation_id = "res-1"
        reservation_key = f"lde:reservations:{reservation_id}"

        self.redis.hset(reservation_key, mapping={"status": "pending"})
        self.redis.sadd(f"{reservation_key}:resources", "boolean-s1i3")
        self.redis.zadd("lde:resources:boolean-s1i3:queues:priorities", {"normal": 0})

        status, external_session_id, position, url, message, assigned_resource = self.script(args=[reservation_id])

        self.assertEqual("pending", status)
        self.assertFalse(external_session_id)
        self.assertFalse(position)
        self.assertFalse(url)
        self.assertFalse(message)
        self.assertFalse(assigned_resource)

    def test_quarantined_owner_blocks_next_allocation(self):
        assign = self.redis.register_script((ROOT / 'labdiscoveryengine/lua/assign_reservation_to_resource.lua').read_text())
        self.redis.set('lde:resources:resource-1:assigned', 'old-owner')
        self.redis.zadd('lde:resources:resource-1:queues:priorities', {'normal': 0})
        self.redis.rpush('lde:resources:resource-1:queues:normal', 'next-request')
        self.assertFalse(assign(args=['resource-1']))
        self.assertEqual(self.redis.lrange('lde:resources:resource-1:queues:normal', 0, -1), ['next-request'])
        self.assertEqual(self.redis.get('lde:resources:resource-1:assigned'), 'old-owner')

    def test_new_assignment_does_not_expire_into_reallocation(self):
        assign = self.redis.register_script((ROOT / 'labdiscoveryengine/lua/assign_reservation_to_resource.lua').read_text())
        self.redis.zadd('lde:resources:resource-1:queues:priorities', {'normal': 0})
        self.redis.rpush('lde:resources:resource-1:queues:normal', 'request-1')
        self.redis.hset('lde:reservations:request-1',mapping={'status':'pending','metadata':'{}'})
        self.assertEqual(assign(args=['resource-1']), 'request-1')
        self.assertEqual(self.redis.ttl('lde:resources:resource-1:assigned'), -1)

    def test_expired_and_terminal_queue_entries_are_skipped_without_ghost_hashes(self):
        assign=self.redis.register_script((ROOT/'labdiscoveryengine/lua/assign_reservation_to_resource.lua').read_text())
        self.redis.zadd('lde:resources:resource-1:queues:priorities', {'normal':0})
        self.redis.rpush('lde:resources:resource-1:queues:normal','expired','finished','valid')
        self.redis.hset('lde:reservations:finished',mapping={'status':'finished','metadata':'{}'})
        self.redis.hset('lde:reservations:valid',mapping={'status':'pending','metadata':'{}'})
        self.assertEqual(assign(args=['resource-1']),'valid')
        self.assertFalse(self.redis.exists('lde:reservations:expired'))
        self.assertFalse(self.redis.hexists('lde:reservations:finished',':assigned'))

    def test_claim_persists_metadata_and_cleanup_reinstates_retention(self):
        import asyncio
        import json
        from unittest.mock import patch
        from tests.test_async_processor_cleanup import build_processor, processor_module
        from labdiscoveryengine.scheduling.sync import web_api
        store=self.redis; processor=build_processor(); key=processor.reservation_keys.base()
        store.hset(key,mapping={'status':'queued','metadata':json.dumps({'user_identifier':'owner'})})
        store.expire(key,1)
        store.sadd(key+':resources','resource-1'); store.expire(key+':resources',1)
        claim_key='lde:external-request:'+processor.reservation_id
        store.setex(claim_key,60,'fingerprint')
        store.zadd('lde:resources:resource-1:queues:priorities',{'normal':0})
        store.rpush('lde:resources:resource-1:queues:normal',processor.reservation_id)
        assign=store.register_script((ROOT/'labdiscoveryengine/lua/assign_reservation_to_resource.lua').read_text())
        self.assertEqual(assign(args=['resource-1']),processor.reservation_id)
        self.assertEqual(store.ttl(key),-1); self.assertEqual(store.ttl(key+':resources'),-1)
        self.assertEqual(store.ttl(claim_key),-1)
        # Admission remains controllable after the independent user index expires.
        with patch.object(web_api,'redis_store',store):
            self.assertTrue(web_api._owns_reservation('owner',processor.reservation_id))
            self.assertFalse(web_api._owns_reservation('other',processor.reservation_id))
            self.assertFalse(web_api.cancel_reservation('other',processor.reservation_id))
            self.assertTrue(web_api.cancel_reservation('owner',processor.reservation_id))
        self.assertEqual(store.hget(key,'status'),'cancelling')
        class Adapter:
            async def eval(self,*args): return store.eval(*args)
        with patch.object(processor_module,'aioredis_store',Adapter()):
            asyncio.run(processor.deassign(None))
        self.assertFalse(store.exists(processor.resource_keys.assigned()))
        self.assertGreater(store.ttl(key),3500)
        self.assertGreater(store.ttl(key+':resources'),3500)
        self.assertGreater(store.ttl(claim_key),604700)

    def test_valid_request_is_exclusive_across_pool_resources(self):
        assign=self.redis.register_script((ROOT/'labdiscoveryengine/lua/assign_reservation_to_resource.lua').read_text())
        key='lde:reservations:pooled'
        self.redis.hset(key,mapping={'status':'pending','metadata':'{}'})
        for resource in ('a','b'):
            self.redis.zadd('lde:resources:'+resource+':queues:priorities',{'normal':0})
            self.redis.rpush('lde:resources:'+resource+':queues:normal','pooled')
        self.assertEqual(assign(args=['a']),'pooled')
        self.assertFalse(assign(args=['b']))
        self.assertFalse(self.redis.exists('lde:resources:b:assigned'))

    def test_status_reports_actual_assignment_not_candidate(self):
        self.redis.hset('lde:reservations:ready-1', mapping=dict(status='ready',resource='resource-2',url='https://lab.invalid'))
        result=self.script(args=['ready-1'])
        self.assertEqual(result[5], 'resource-2')

    def test_atomic_replay_claim_excludes_competing_admissions_and_old_hashes(self):
        from concurrent.futures import ThreadPoolExecutor
        from unittest.mock import patch
        from labdiscoveryengine.views import external
        with patch.object(external,'redis_store',self.redis):
            with ThreadPoolExecutor(max_workers=8) as workers:
                outcomes=list(workers.map(lambda _: external.claim_external_request('race','fingerprint'),range(16)))
            self.assertEqual(outcomes.count(1),1)
            self.assertEqual(outcomes.count(0),15)
            self.assertEqual(external.claim_external_request('race','different'),-1)
            self.redis.hset('lde:reservations:old',mapping={'status':'broken','metadata':'old'})
            self.assertEqual(external.claim_external_request('old','new'),-2)
            self.assertEqual(self.redis.hget('lde:reservations:old','metadata'),'old')

    def test_real_external_late_replay_does_not_reset_owned_state(self):
        import asyncio
        import json
        from unittest.mock import patch
        from tests.views.test_external import ExternalTestCase
        from labdiscoveryengine.views import external
        from labdiscoveryengine.scheduling.sync import web_api
        from labdiscoveryengine.scheduling.asyncio import processor as pm
        from labdiscoveryengine.utils import lde_config
        from labdiscoveryengine.scheduling.data import ReservationRequest
        store=self.redis
        ExternalTestCase.setUpClass()
        case=ExternalTestCase()
        old_scripts=web_api.sync_lua_scripts._SCRIPT_INSTANCES.copy()
        try:
            with patch.object(external,'redis_store',store),patch.object(web_api,'redis_store',store),patch.object(web_api,'is_mongo_active',return_value=False):
                web_api.sync_lua_scripts.initialize_web_lua_scripts()
                body=dict(laboratory='dummy',resources=['fpga-1'],userIdentifier='tester',backUrl='https://offline.invalid',requestId='late-replay-123456789')
                first=case.client.post('/external/v1/reservations/',json=body,headers=case._auth_headers())
                self.assertEqual(first.status_code,200)
                identifier=first.json['reservation_id'];key='lde:reservations:'+identifier
                claim_key='lde:external-request:'+identifier
                assign=store.register_script((ROOT/'labdiscoveryengine/lua/assign_reservation_to_resource.lua').read_text())
                self.assertEqual(assign(args=['fpga-1']),identifier)
                self.assertEqual(store.ttl(claim_key),-1)
                store.hset(key,mapping=dict(status='ready',session_id='original-session',resource='fpga-1',url='https://offline.invalid',start_attempted='1'))
                # Simulate an old deployment's expired seven-day replay key.
                store.pexpire(claim_key,1);time.sleep(.02)
                for payload,expected in ((body,200),(dict(body,userIdentifier='different'),409)):
                    result=case.client.post('/external/v1/reservations/',json=payload,headers=case._auth_headers())
                    self.assertEqual(result.status_code,expected)
                    self.assertEqual(store.hget(key,'status'),'ready')
                    self.assertEqual(store.ttl(key),-1)
                # Defense in depth even if some other caller bypasses HTTP claim.
                with self.assertRaises(redis.ResponseError):
                    web_api.sync_lua_scripts.store_reservation(ReservationRequest.fromdict(json.loads(store.hget(key,'metadata'))))
                class Adapter:
                    def __getattr__(self,name):
                        async def method(*a,**k): return getattr(store,name)(*a,**k)
                        return method
                class Lab:
                    starts=0
                    finished=[]
                    async def __aenter__(self): return self
                    async def __aexit__(self,*a): pass
                    async def start(self,request): self.starts+=1; raise AssertionError('Second start')
                    async def get_should_finish(self,session): return -1
                    async def finish(self,session): self.finished.append(session);return -1
                processor=pm.ResourceReservationProcessor(lde_config.resources['fpga-1'],identifier);lab=Lab()
                with patch.object(pm,'aioredis_store',Adapter()),patch.object(pm,'is_mongo_active',return_value=False),patch.object(processor,'get_client',return_value=lab):
                    asyncio.run(processor.process())
                self.assertEqual(lab.starts,0)
                self.assertEqual(lab.finished,['original-session'])
                self.assertFalse(store.exists('lde:resources:fpga-1:assigned'))
        finally:
            web_api.sync_lua_scripts._SCRIPT_INSTANCES=old_scripts
            ExternalTestCase.tearDownClass()
