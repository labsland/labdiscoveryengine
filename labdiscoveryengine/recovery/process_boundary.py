"""Kernel process boundaries, including cgroup v1 on qualified legacy hosts.

Managed programs enter before exec and drop root privileges. Their descendants
inherit membership even after setsid/double-fork; app users cannot move them.
No executable-name killing and no resource limits are imposed.
"""
import os
from pathlib import Path
import signal
import time


class ProcessBoundary:
    def __init__(self, path, version):
        self.path = Path(path)
        self.version = version
        expected = '/sys/fs/cgroup/freezer/labsland-recovery/' if version == 1 else '/sys/fs/cgroup/labsland-recovery/'
        if not str(self.path).startswith(expected) or len(self.path.parts) != len(Path(expected).parts) + 1:
            raise ValueError('Exact managed cgroup path required')
        if self.path.is_symlink():
            raise ValueError('Symlink cgroup rejected')

    def prepare(self):
        self.path.mkdir(parents=True, exist_ok=True)
        required = 'freezer.state' if self.version == 1 else 'cgroup.freeze'
        if not (self.path / required).is_file():
            raise RuntimeError('Kernel process-boundary capability unavailable')
        # The application uid must never be allowed to move itself/children out.
        for path in (self.path, self.path / 'cgroup.procs'):
            info = path.stat()
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise RuntimeError('Cgroup delegated to an untrusted writer')

    def pids(self):
        if not self.path.exists():
            return []
        # No descendant cgroups may be created by applications in this boundary.
        if any(p.is_dir() for p in self.path.iterdir()):
            raise RuntimeError('Unexpected nested cgroup')
        return [int(p) for p in (self.path / 'cgroup.procs').read_text().split()]

    def frozen(self):
        if self.version == 1:
            return (self.path / 'freezer.state').read_text().strip() == 'FROZEN'
        return 'frozen 1' in (self.path / 'cgroup.events').read_text().splitlines()

    def freeze(self, enabled):
        name = 'freezer.state' if self.version == 1 else 'cgroup.freeze'
        value = ('FROZEN' if enabled else 'THAWED') if self.version == 1 else ('1' if enabled else '0')
        (self.path / name).write_text(value)
        if enabled:
            deadline = time.monotonic() + 10
            while not self.frozen():
                if time.monotonic() > deadline:
                    raise RuntimeError('Kernel did not freeze all old executors')
                time.sleep(.02)

    def enter(self):
        self.prepare()
        if self.pids():
            raise RuntimeError('Old execution generation still occupies cgroup')
        self.freeze(False)
        (self.path / 'cgroup.procs').write_text(str(os.getpid()))
        if os.getpid() not in self.pids():
            raise RuntimeError('Process did not enter managed cgroup')

    def terminate(self):
        self.prepare()
        self.freeze(True)
        try:
            if self.version == 2 and (self.path / 'cgroup.kill').exists():
                (self.path / 'cgroup.kill').write_text('1')
            else:
                # Frozen processes cannot fork, detach or voluntarily exit while
                # this membership snapshot is killed. Keep the boundary frozen
                # on freeze failure; never interpret a timeout as completion.
                for pid in self.pids():
                    try: os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError: pass
        finally:
            self.freeze(False)  # allows the pending SIGKILL to complete on v1
        deadline = time.monotonic() + 15
        while self.pids():
            if time.monotonic() > deadline:
                raise RuntimeError('Old executors remain in kernel boundary')
            time.sleep(.05)
