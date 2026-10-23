Qualified serial recovery
=========================

This optional coordinator is provisioned by ll-installer for explicit resources.
It is disabled by default. It uses a distinct non-expiring ``:maintenance`` key,
never a synthetic reservation. Acquisition yields to assignments, queues and
in-flight health probes. Allocation checks maintenance before popping a queue;
normal discovery shows a maintained resource as unavailable.

The scheduler must start with ``SERIAL_RECOVERY_STATE_DIR`` and a comma-separated
``SERIAL_RECOVERY_RESOURCES`` list. Before publishing its Redis-epoch capability,
it restores unfinished coordinator and health-probe intentions from durable
journals. Old epochs cannot allocate. A health probe records durable intent and
revalidates its captured epoch/token before sending HTTP. Cancellation or timeout
retains uncertainty: canceling a client does not stop the backend's hardware work.
Completed probes close their own journals and compare-delete their own markers.

The coordinator's managed private JSON names scheduler/backend stores, a durable
state directory, and exact resource/controller/readiness/by-id/programmer prefixes.
All programmer prefixes for a physical resource must be inventoried. Backend
sessions, pending tasks and ownership/cleanup state must be idle. No customer
session, reservation or queue is cleared. The controller requires the same
operation token and worker generation; retries do not reopen a transport twice.

A lost command response, ownership change, Redis loss or failed probe leaves the
resource excluded. A subsequent timer invocation reports reconciliation-required
and does not replay the operation. ``--inspect`` shows pending operations.
``--reconcile EXACT_RESOURCE`` may finish an already successful worker operation
under the same exact idle ownership; it never reconnects CDC. New worker
generations, failed receipts, unresolved probe intentions, and unknown transport
outcomes require independent operator/hardware reconciliation. Preserve journals
and existing fences. Do not expire ownership or mutate reservations to unblock it.

Qualification and updates
-------------------------

Enable only after source/ABI review and a physical canary. Activate this scheduler
version while its entire scope is safely drained, so all allocation writers honor
the contract and old in-flight backend probes are accounted for. Stop the owned
coordinator timer before updating its controller/runtime; do not interrupt an
active coordinator. ll-installer refuses pending receipts and preserves journal
storage outside replaceable code directories. The initial installer policy uses
manual qualification; automatic polling is a separate reviewed configuration.

The controller must be one physical resource per worker and use firmware qualified
for CDC interface0 open/close without reset. A normal readiness success is not a
substitute for full physical defaults, programming, simulation/UART and production
user-flow acceptance. Preserve independent historical lab failures; this transport
recovery does not establish their cause or authorize broader resets.
