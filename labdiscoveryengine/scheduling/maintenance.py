"""Internal, idle-only maintenance ownership; never a synthetic reservation.

There is deliberately no TTL or unconditional release. A coordinator crash or
unknown hardware outcome requires explicit reconciliation. The caller must
also exclude backend sessions/tasks and verify hardware completion before
release; LDE ownership alone does not establish backend readiness.
"""
import re


ACQUIRE = """
local base = KEYS[1]
if redis.call('exists', base .. ':recovery-hold') == 1 then return 0 end
if redis.call('exists', base .. ':maintenance') == 1 then return 0 end
if redis.call('exists', base .. ':assigned') == 1 then return 0 end
if redis.call('exists', base .. ':health-probe') == 1 then return 0 end
for _, priority in ipairs(redis.call('zrange', base .. ':queues:priorities', 0, -1)) do
    if redis.call('llen', base .. ':queues:' .. priority) > 0 then return 0 end
end
redis.call('set', base .. ':maintenance', ARGV[1])
return 1
"""

BEGIN_PROBE = """
local info = redis.call('info', 'server')
local run_id = string.match(info, 'run_id:([^\\r\\n]+)')
if run_id ~= ARGV[2] or redis.call('get', 'lde:running') ~= 'true' then return 0 end
if redis.call('exists', KEYS[1] .. ':maintenance', KEYS[1] .. ':recovery-hold') > 0 then return 0 end
return redis.call('set', KEYS[1] .. ':health-probe', ARGV[1], 'NX') and 1 or 0
"""

END_PROBE = """
if redis.call('get',KEYS[1] .. ':health-probe') ~= ARGV[1] then return 0 end
return redis.call('del',KEYS[1] .. ':health-probe')
"""

CHECK_PROBE = """
local run_id = string.match(redis.call('info','server'), 'run_id:([^\\r\\n]+)')
if run_id ~= ARGV[2] or redis.call('get','lde:running') ~= 'true' then return 0 end
if redis.call('exists',KEYS[1] .. ':maintenance',KEYS[1] .. ':recovery-hold') > 0 then return 0 end
return redis.call('get',KEYS[1] .. ':health-probe') == ARGV[1] and 1 or 0
"""

RELEASE = """
local base = KEYS[1]
if redis.call('get', base .. ':maintenance') ~= ARGV[1] then return 0 end
if redis.call('exists', base .. ':assigned') == 1 then return 0 end
redis.call('del', base .. ':maintenance')
redis.call('publish', base .. ':channel', 'maintenance-released')
return 1
"""


def _validate(resource, owner):
    if not isinstance(resource, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', resource):
        raise ValueError('Exact resource identifier required')
    if not isinstance(owner, str) or not re.fullmatch(r'[0-9a-f]{32}', owner):
        raise ValueError('Unique operation token required')
    return 'lde:resources:' + resource


def acquire(redis, resource, owner):
    """Atomically yield to any active reservation or already queued work."""
    return redis.eval(ACQUIRE, 1, _validate(resource, owner), owner) == 1


def owned(redis, resource, owner):
    base = _validate(resource, owner)
    current, assigned = redis.mget(base + ':maintenance', base + ':assigned')
    return current in (owner, owner.encode('ascii')) and assigned is None


def release_verified(redis, resource, owner):
    """Release only this owner, AFTER external completion/readiness verification."""
    return redis.eval(RELEASE, 1, _validate(resource, owner), owner) == 1
