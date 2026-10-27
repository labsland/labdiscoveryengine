Managed laboratory recovery
===========================

The optional ``LAB_RECOVERY_STATE_FILE`` contract separates laboratory recovery
from reservation lifetime. Interrupted reservations become terminal errors;
students may make a new reservation after a qualified physical baseline. Old
sessions are never resumed.

The ``recovery`` package is also built as the small ``labsland-recovery`` runtime
using ``pip wheel ./recovery-runtime``. Root-owned deployment profiles name exact
process groups, Redis namespaces and qualified component restore adapters. The
coordinator closes admission, fences old executors with Linux cgroups, retires
scoped queued work, restores and verifies each resource, and then publishes its
new generation. Independent failed resources and maintenance holds stay closed.
Healthy resources and ordinary virtual-peripheral activity do not cause resets.

Assignments capture the generation atomically. Processor transitions check the
same reservation owner, generation and Redis run identity. Session-administration
requests carry ``X-Lab-Recovery-Epoch``; supporting backends reject stale epochs.
Backend/runner/controller Redis clients are sealed before their application
initializes, preventing old consumers from reconnecting to restored queues.

A profile is not qualified merely because services start. It must enumerate all
actuation producers/executors, use non-overlapping retirement namespaces, verify
its physical baseline, and pass interrupted-session/new-session tests. The
installer owns deployment mappings, source pins, protected-file hashes and
rollback receipts. See ll-installer's managed-recovery deployment documentation
for the coordinated release workflow. Existing deployments remain opt-out.
