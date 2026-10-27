"""Read-only readiness projection using the existing provider health contract."""
import argparse
import datetime
import json
import threading
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .admission import snapshot
from .managed import ManagedAdapter, load_plan, execute


def status(plan):
    state=snapshot(str(Path(plan['state_directory'])/'state.json'),plan['stores'])
    adapter=ManagedAdapter(plan)
    current=adapter.scheduler_identity()
    service_ready=(current == state.get('scheduler_identity') and
                   all(row['state']=='RUNNING' for row in current['programs'].values()))
    execute(plan['storage_validate_command'])
    try:
        with urllib.request.urlopen(plan['web_probe_url'], timeout=3):
            service_ready = False
    except urllib.error.HTTPError as error:
        service_ready = service_ready and error.code == 401
    resources={}
    for name in adapter.resources:
        item=state['resources'].get(name,{})
        base='lde:resources:'+name
        health=adapter.scheduler.hgetall(base+':health')
        owned=bool(adapter.scheduler.exists(base+':assigned'))
        working=False
        try:
            when=datetime.datetime.fromisoformat(health['checked_at'])
            if when.tzinfo is None:when=when.replace(tzinfo=datetime.timezone.utc)
            age=(datetime.datetime.now(datetime.timezone.utc)-when).total_seconds()
            working=health.get('status')=='healthy' and age >= -60 and (owned or age <= 120)
        except (KeyError,ValueError,TypeError):pass
        working=(service_ready and item.get('phase')=='ready' and working and
                 not adapter.scheduler.exists(base+':recovery-hold',base+':maintenance'))
        resources[name]=dict(working=bool(working),code='' if working else 'laboratory-recovering')
    adapter.scheduler.close()
    return dict(version=1,service_ready=service_ready,
                checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),resources=resources)


class ReadinessCache:
    """One background producer; HTTP clients never trigger privileged probes."""
    def __init__(self, probe, clock=time.monotonic):
        self.probe = probe
        self.clock = clock
        self.lock = threading.Lock()
        self.payload = None
        self.at = 0

    def refresh(self):
        try:
            payload = self.probe()
        except Exception:
            payload = dict(version=1, service_ready=False, resources={},
                           checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
        with self.lock:
            self.payload, self.at = payload, self.clock()

    def get(self):
        with self.lock:
            if self.payload is None or self.clock() - self.at > 15:
                return dict(version=1, service_ready=False, resources={}, code='health-stale')
            return self.payload

    def run(self):
        while True:
            self.refresh()
            time.sleep(3)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--plan',required=True)
    args=parser.parse_args();plan=load_plan(args.plan)
    cache=ReadinessCache(lambda: status(load_plan(args.plan)))
    threading.Thread(target=cache.run, daemon=True).start()

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        slots = threading.BoundedSemaphore(4)
        def process_request(self, request, address):
            if not self.slots.acquire(blocking=False):
                self.shutdown_request(request)
                return
            try:
                super().process_request(request, address)
            except BaseException:
                self.slots.release()
                raise
        def process_request_thread(self, request, address):
            try:
                request.settimeout(5)
                super().process_request_thread(request,address)
            finally:
                self.slots.release()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            if self.path!='/health':self.send_error(404);return
            payload=cache.get()
            data=json.dumps(payload).encode()
            self.send_response(200 if payload['service_ready'] else 503)
            self.send_header('Content-Type','application/json')
            self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    Server(('127.0.0.1',plan['health_port']),Handler).serve_forever()


if __name__=='__main__':main()
