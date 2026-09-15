"""Exercise both real protocol clients through the cleanup processor."""
import unittest
from unittest.mock import patch
from labdiscoveryengine.scheduling.asyncio.client import LabDiscoveryLibResourceClient, WebLabLibResourceClient
from tests.test_async_processor_cleanup import FakeAsyncRedis, build_processor, processor_module


class CleanupHttpContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_real_client_waits_for_explicit_completion(self):
        bodies=[{'should_finish':0},{'message':'Deleted'}]
        class Response:
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
            def raise_for_status(self): pass
            async def json(self): return bodies.pop(0)
        class Session:
            def delete(self,*args,**kwargs): return Response()
        processor=build_processor(); client=object.__new__(LabDiscoveryLibResourceClient)
        client.resource=processor.resource; client.client_session=Session(); processor.client=client
        store=FakeAsyncRedis(); owner=processor.resource_keys.assigned(); key=processor.reservation_keys.base()
        store.values[owner]=processor.reservation_id; store.values[key]={'status':'ready'}
        async def during_wait(seconds):
            self.assertIn(owner,store.values)
            self.assertEqual(store.values[key]['status'],'finishing')
        with patch.object(processor_module,'aioredis_store',store),patch.object(processor_module,'is_mongo_active',return_value=False),patch.object(processor_module.asyncio,'sleep',side_effect=during_wait):
            await processor.finish(None,'session')
        self.assertEqual(bodies,[])
        self.assertNotIn(owner,store.values)

    async def test_errors_missing_data_and_protocol_success(self):
        cases=[(503,{'error':True},False), (200,{'error':True,'should_finish':-1},False),
               (200,{'success':False,'message':'Deleted'},False), (200,{},False),
               (200,{'message':'Not found'},False), (200,{'message':'Unknown op'},False),
               (200,[],False), (200,{'should_finish':True},False),
               (200,{'should_finish':float('nan')},False),
               (200,{'should_finish':'-1'},False),
               (200,{'message':'Deleted'},True), (200,{'should_finish':-1},True)]
        for client_type in (LabDiscoveryLibResourceClient,WebLabLibResourceClient):
            for status,body,completed in cases:
                with self.subTest(client=client_type.__name__,status=status,body=body):
                    methods=[]
                    class Response:
                        async def __aenter__(self): return self
                        async def __aexit__(self,*args): pass
                        def raise_for_status(self):
                            if status>=400: raise ValueError('HTTP failure')
                        async def json(self): return body
                    class Session:
                        def delete(self,*args,**kwargs): methods.append('DELETE'); return Response()
                        def post(self,*args,**kwargs): methods.append('POST'); return Response()
                    processor=build_processor(); client=object.__new__(client_type)
                    client.resource=processor.resource; client.client_session=Session(); processor.client=client
                    store=FakeAsyncRedis(); key=processor.reservation_keys.base(); owner=processor.resource_keys.assigned()
                    store.values[owner]=processor.reservation_id
                    store.values[key]={'status':'cancelling','start_attempted':'1'}
                    with patch.object(processor_module,'aioredis_store',store),patch.object(processor_module,'is_mongo_active',return_value=False):
                        await processor.finish(None,'session')
                    self.assertEqual(store.values[key]['status'],'finished' if completed else 'broken')
                    self.assertEqual(owner in store.values,not completed)
                    self.assertEqual(methods,['DELETE' if client_type is LabDiscoveryLibResourceClient else 'POST'])
