"""Version1 recovery receipt contract. Readiness is not process liveness.

Kept independent of Flask so packaging can install this small shared runtime
module beside the managed launchers used by LDE and backend applications.
"""
import json
from pathlib import Path
import redis
from .state import trusted_read


class Unavailable(RuntimeError):
    pass


def snapshot(path, stores, resource=None, client_factory=redis.Redis.from_url,
             boot_path='/proc/sys/kernel/random/boot_id'):
    try:
        state = trusted_read(path)
        if state.get('version') != 1 or state.get('phase') != 'managed':
            raise Unavailable('laboratory-recovering')
        if state['generation']['boot'] != Path(boot_path).read_text().strip():
            raise Unavailable('host-restarted')
        if set(stores) != set(state['generation']['stores']):
            raise Unavailable('store-configuration-changed')
        for name, url in stores.items():
            connection = client_factory(url, decode_responses=True,
                                        socket_connect_timeout=1, socket_timeout=1)
            try:
                if connection.info('server')['run_id'] != state['generation']['stores'][name]:
                    raise Unavailable('storage-restarted')
            finally:
                connection.close()
        if resource is not None:
            item = state['resources'].get(resource, {})
            if item.get('phase') != 'ready':
                raise Unavailable('resource-recovering')
            return item['epoch']
        return state
    except Unavailable:
        raise
    except Exception as error:
        raise Unavailable('recovery-state-unavailable') from error


def install_backend_guard(app, path, stores, resource):
    from flask import request, jsonify
    @app.before_request
    def recovery_guard():
        # Liveness/read-only health is available while admission is closed.
        if request.method == 'GET' and request.path.endswith('/healthcheck'):
            return None
        try:
            epoch = snapshot(path, stores, resource)
            # Session administration is issued only by the enabled scheduler.
            # Old in-flight processors cannot create/dispose a new-era session.
            if '/weblab/sessions/' in request.path or '/ldl/sessions/' in request.path:
                if request.headers.get('X-Lab-Recovery-Epoch') != epoch:
                    raise Unavailable('interrupted-session')
        except Unavailable as error:
            return jsonify(success=False, error=str(error)), 503
