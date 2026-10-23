import asyncio
from labdiscoveryengine.utils import create_proxied_instance

class Client:
    def __init__(self,value):self.value=value
    def read(self):return self.value
    async def read_async(self):return self.value

def test_replacing_client_drops_bound_method_cache():
    proxy=create_proxied_instance(Client)
    proxy.set_proxied_object(Client('old'))
    assert proxy.read()=='old' and asyncio.run(proxy.read_async())=='old'
    proxy.set_proxied_object(Client('new'))
    assert proxy.read()=='new' and asyncio.run(proxy.read_async())=='new'
