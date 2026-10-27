"""Seal Redis connections and pipelines to the execution generation at launch.

A restarted server drops its sockets. Every new socket is verified before an
application command is sent, independently of redis-py's on_connect variants.
The kernel boundary handles old hardware operations already in flight.
"""
import json
import os
import re
import weakref
from urllib.parse import urlsplit
import redis


class RedisGenerationChanged(redis.RedisError):
    """Permanent for this process: only a new managed launch can accept a store."""


def store_identity(pool):
    kwargs = pool.connection_kwargs
    if kwargs.get('path'):
        return dict(path=kwargs['path'], db=int(kwargs.get('db',0)))
    host = kwargs.get('host','localhost')
    if host == 'localhost': host = '127.0.0.1'
    return dict(host=host, port=int(kwargs.get('port',6379)), db=int(kwargs.get('db',0)))


def connection_class(base, expected_run_id, expected_db=0):
    if not re.fullmatch(r'[0-9a-f]{40}', expected_run_id or ''):
        raise ValueError('An authorized Redis launch generation is required')

    class GenerationConnection(base):
        _generation_socket = None
        _generation_connecting = False

        def connect(self, *args, **kwargs):
            self._generation_connecting = True
            callbacks = self._connect_callbacks
            self._connect_callbacks = []
            try:
                super().connect(*args, **kwargs)
                if self._sock is None:
                    raise RedisGenerationChanged('Redis transport did not open a socket')
                if self._sock is self._generation_socket:
                    return
                if int(self.db) != expected_db:
                    raise RedisGenerationChanged('Redis database differs from the managed namespace')
                self.send_command('INFO', 'server')
                info = self.read_response()
                if isinstance(info, bytes): info = info.decode('ascii')
                match = re.search(r'^run_id:([0-9a-f]{40})\r?$', info, re.MULTILINE)
                if not match or match.group(1) != expected_run_id:
                    raise RedisGenerationChanged('Redis generation changed; laboratory recovery required')
                self._generation_socket = self._sock
            except BaseException:
                self.disconnect()
                raise
            finally:
                self._connect_callbacks = callbacks
                self._generation_connecting = False
            # PubSub resubscription sends application commands. It must happen
            # after INFO proves the generation and outside the handshake bypass.
            for registered in callbacks:
                callback = registered() if isinstance(registered, weakref.ReferenceType) else registered
                if callback is not None:
                    callback(self)

        def send_packed_command(self, *args, **kwargs):
            # Base connect may authenticate/select a DB before the INFO check.
            # Ordinary traffic is never allowed through that bootstrap bypass.
            if not self._generation_connecting and (self._sock is None or self._sock is not self._generation_socket):
                self.connect()
            return super().send_packed_command(*args, **kwargs)

        def disconnect(self, *args, **kwargs):
            self._generation_socket = None
            return super().disconnect(*args, **kwargs)
    return GenerationConnection


def fenced_redis(url, **kwargs):
    """Supported client factory, installed before worker/task initialization."""
    if urlsplit(url).scheme not in ('redis','rediss','unix') or kwargs.get('redis_connect_func'):
        raise ValueError('Unsupported managed Redis transport')
    kwargs.setdefault('socket_connect_timeout', 5)
    kwargs.setdefault('socket_timeout', 10)
    bindings = json.loads(os.environ['LAB_RECOVERY_EXPECTED_REDIS_STORES'])
    pool = redis.ConnectionPool.from_url(url, **kwargs)
    identity = store_identity(pool)
    matches = [binding['run_id'] for binding in bindings if binding['identity'] == identity]
    if len(matches) != 1:
        raise ValueError('Resolved Redis host/database is not uniquely bound by the deployment')
    pool.connection_class = connection_class(pool.connection_class, matches[0], identity['db'])
    client = redis.Redis(connection_pool=pool)
    client.ping()
    return client


class FencedRedisProvider:
    """FlaskRedis's supported custom-provider API."""
    from_url = staticmethod(fenced_redis)
