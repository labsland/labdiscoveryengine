"""Read-only qualification of standard GPIO after its queued reset completes."""
import hashlib
from pathlib import Path
import time
import yaml


def wait_worker(plan, epoch, identity, connection, require_epoch, sleep=time.sleep, clock=time.monotonic):
    """Wait for completed worker iterations before issuing a physical reset.

    Supervisor RUNNING only proves the process stayed alive. Legacy GPIO
    initialization can take tens of seconds before the task loop starts.
    """
    config = plan['gpio_readback']
    timeout = config.get('startup_timeout', 120)
    if type(timeout) not in (int, float) or not 1 <= timeout <= 120:
        raise ValueError('Bounded controller startup timeout required')
    before = identity()
    previous = connection.get(config['heartbeat_key'])
    samples = 0
    deadline = clock() + timeout
    while clock() < deadline:
        require_epoch(epoch)
        current = connection.get(config['heartbeat_key'])
        if current and current != previous:
            samples += 1
        previous = current
        if samples >= 2:
            if identity() != before:
                raise RuntimeError('Controller changed during startup')
            return dict(profile='controller-worker-ready-v1', heartbeat_samples=samples)
        sleep(.2)
    raise RuntimeError('Controller worker did not finish initialization')


def verify(plan, epoch, identity, connection, require_epoch, sleep=time.sleep, clock=time.monotonic,
           gpio_root=Path('/sys/class/gpio')):
    config = plan['gpio_readback']
    filename = config['configuration']
    if filename not in plan['protected_files']:
        raise ValueError('GPIO identity must be a protected deployment file')
    raw = Path(filename).read_bytes()
    if hashlib.sha256(raw).hexdigest() != plan['protected_files'][filename]:
        raise ValueError('GPIO mapping drift')
    instances = yaml.safe_load(raw)
    outputs = instances[config['instance']]['outputs']
    if not outputs or any('gpio_num' not in value or 'pico_num' in value for value in outputs.values()):
        raise ValueError('Readback only qualifies standard sysfs GPIO')
    before = identity()
    deadline = clock() + 30
    heartbeat = connection.get(config['heartbeat_key'])
    samples = 0
    values = {}
    while clock() < deadline:
        require_epoch(epoch)
        current = connection.get(config['heartbeat_key'])
        empty = all(connection.llen(key) == 0 for key in config['task_queues'])
        values = {}
        for name, row in outputs.items():
            pin = row['gpio_num']
            if not isinstance(pin,int) or isinstance(pin,bool) or pin < 0:
                raise ValueError('Exact nonnegative GPIO pin required')
            path = gpio_root / ('gpio%d' % pin)
            if (path/'direction').read_text().strip() != 'out':
                raise RuntimeError('GPIO direction differs from the declared output')
            value = (path/'value').read_text().strip()
            if value not in ('0','1'): raise RuntimeError('Invalid physical GPIO sample')
            values[name] = value == '1'
        baseline = all(values[name] == bool(row.get('active_low',False)) for name,row in outputs.items())
        # A new heartbeat follows the worker's preceding complete task/ticker
        # iteration; two samples exclude an acknowledgement of queueing alone.
        if empty and baseline and current and current != heartbeat:
            samples += 1
        elif not empty or not baseline:
            samples = 0
        heartbeat = current
        if samples >= 2:
            after = identity()
            if before != after: raise RuntimeError('Controller changed during readback')
            for program in config['required_stopped']:
                if after['programs'][program]['state'] not in ('STOPPED','EXITED'):
                    raise RuntimeError('Virtual peripheral remains active')
            return dict(profile='standard-sysfs-gpio-v1', configuration_sha256=hashlib.sha256(raw).hexdigest(),
                        outputs=values, heartbeat_samples=samples, verified_at=time.time())
        sleep(.2)
    raise RuntimeError('Fresh worker and physical GPIO baseline were not confirmed')
