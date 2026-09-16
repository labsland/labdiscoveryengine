Redis durability and recovery
=============================

Production requirement
----------------------

LDE uses Redis for queues, reservation metadata, ownership and lifecycle state.
This is operational state, not a reconstructible cache. Production deployments
must provide persistent Redis storage and a fail-closed recovery procedure.
Setting ``REDIS_URL`` does not configure persistence. Disposable development/test
Redis must never control physical laboratories.

Recommended baseline:

* Dedicated Redis process or equivalent isolated managed database on durable
  storage. Logical database numbers do not isolate persistence.
* AOF enabled with ``appendfsync everysec`` or ``always``; periodic snapshots for
  backups. Snapshots alone can lose substantial recent state.
* ``maxmemory-policy noeviction``: expose capacity errors rather than silently
  losing ownership. Monitor memory, disk, AOF write/rewrite and snapshot failures.
* Restricted access and tested backups: metadata can contain personal information
  and session credentials. One persistent process is not high availability.

Every-second fsync can lose recent writes during a crash. Even synchronous
persistence cannot atomically commit both Redis and a physical lab's HTTP side
effects. Stored state must not be mistaken for proof of hardware state.

Recovery contract
-----------------

After Redis restart, state loss, a missing data directory or old-backup restore:

#. Stop or fence admission and all workers sharing that store.
#. Inspect assignments, session identities and unfinished start/cleanup attempts.
   Verify actual lab sessions and hardware-access agents as appropriate.
#. Keep uncertain resources unavailable. Do not clear ownership to unblock queues,
   extend expired grants, or repeat a potentially successful start request.
#. Resume admission only after ownership is reconciled. Missing Redis records do
   not prove an idle board.

Persistence does not perform these steps or fix lifecycle bugs. Qualify scheduler
versions and adapters separately for lost responses, cancellation and cleanup.
``lde:running`` is a worker synchronization hint, not recovery authorization.

Planned migration
-----------------

Drain users and stop all writers before copying a scheduler database. Preserve
values/types and absolute expirations, verify the copy and retain restricted
rollback artifacts. Switch web and workers together. Never run two schedulers
with independent ownership copies for the same boards. Once new reservations
begin, rollback requires another drain/reconciliation; never restore an old
snapshot over live ownership. Do not use ``FLUSHDB``/``FLUSHALL`` for recovery.

The LabsLand ILB installer integration supplies dedicated AOF/noeviction Redis,
a storage health check and a Redis-run-ID approval gate. Redis refuses to start
while LDE processes remain running; every new Redis process requires operator
reconciliation before LDE starts. This intentionally trades automatic restart
availability for controlled recovery. These deployment safeguards are NOT
automatically installed by ``pip install labdiscoveryengine``; other operators
need equivalent procedures.

References
----------

* `Redis persistence <https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/>`_
* `Logical databases <https://redis.io/docs/latest/commands/select/>`_
