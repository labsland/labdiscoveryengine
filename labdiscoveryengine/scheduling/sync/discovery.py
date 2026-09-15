"""Safe external inventory: configuration and Redis snapshots, never lab probes."""
import hashlib
import json
import time

from labdiscoveryengine.utils import lde_config
from labdiscoveryengine.scheduling.keys import ResourceKeys
from labdiscoveryengine.scheduling.sync.web_api import redis_store


def discover_resources(laboratory):
    lab = lde_config.laboratories[laboratory]
    names = sorted(lab.resources)
    # A malformed/oversized configuration must not turn one HTTP request into
    # unbounded work. This limit is intentionally well above ordinary lab pools.
    if len(names) > 1000:
        raise ValueError("Laboratory inventory exceeds discovery limit")
    pipe = redis_store.pipeline(transaction=True)
    for name in names:
        pipe.get(ResourceKeys(name).assigned())
        pipe.hgetall(ResourceKeys(name).health())
    snapshots = pipe.execute()
    owners = [snapshots[i * 2] for i in range(len(names))]
    pipe = redis_store.pipeline(transaction=True)
    for owner in owners:
        if owner:
            pipe.hgetall('lde:reservations:' + owner)
    owner_records = iter(pipe.execute())
    resources = []
    for offset, name in enumerate(names):
        resource = lde_config.resources[name]
        assigned, health = snapshots[offset * 2:offset * 2 + 2]
        owner = next(owner_records) if assigned else None
        health_status = health.get('status', 'unknown')
        # No assignment is NOT proof of idle after Redis/worker loss. Present
        # unknown, leaving admission/ownership to the existing scheduler.
        quarantined = assigned and (not owner or owner.get('reconciliation_required') or owner.get('status') == 'broken')
        state = 'unavailable' if health_status == 'broken' or quarantined else ('in_use' if assigned else 'unknown')
        resources.append(dict(id=name, label=name, features=sorted(resource.features),
                              state=state, health=health_status,
                              health_checked_at=health.get('checked_at')))
    revision_data = dict(laboratory=laboratory, max_time=lab.max_time,
                         bypass_resource_health=lab.bypass_resource_health,
                         resources=[dict(id=r['id'], features=r['features'],
                                         url=lde_config.resources[r['id']].url,
                                         api=lde_config.resources[r['id']].api,
                                         login=lde_config.resources[r['id']].login,
                                         password=lde_config.resources[r['id']].password) for r in resources])
    revision = hashlib.sha256(json.dumps(revision_data, sort_keys=True).encode()).hexdigest()
    return dict(version=1, capabilities=['resource_selection', 'assigned_resource', 'idempotent_admission'],
                laboratory=laboratory, revision=revision, observed_at=time.time(),
                max_time=lab.max_time, resources=resources,
                # A bypass lab cannot offer the safety contract of staff targeting.
                selection_safe=not lab.bypass_resource_health)
