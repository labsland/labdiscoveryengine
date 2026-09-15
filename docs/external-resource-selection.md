# External resource discovery and exact selection

`GET /external/v1/laboratories/<laboratory>/resources` uses existing HTTP Basic
external-user authentication. The laboratory must belong to that principal;
unauthorized and missing laboratories both return 404. Responses are `no-store`.

Version 1 returns `laboratory`, `revision`, `observed_at` (Unix seconds),
`max_time`, `selection_safe`, `capabilities` and `resources`. Each resource contains
canonical `id`, `label`, `features`, coarse `state`, `health`, `health_checked_at`.
Only that laboratory's configured members appear. No credentials, service URLs,
other users or reservation identifiers are exposed. Internal routing/configuration
changes affect the opaque revision. A maximum of 1000 resources is supported.
Capabilities include `resource_selection`, `assigned_resource` and
`idempotent_admission`; clients requiring retry-safe admission must check all three.

Discovery reads Redis snapshots, not physical probes. Missing ownership is reported
as `unknown`, not proven idle. A retained broken/unknown owner is unavailable.
Health-bypass laboratories report `selection_safe=false`. Inventory is advisory:
clients must still reserve via the existing API with a nonempty singleton
`resources` list and any required `features`. An empty list still means the whole
pool for backward compatibility, so exact clients must never send one.

Reservation status includes `assigned_resource` when the scheduler records actual
assignment. It is not an echo of the requested resource. Existing clients can
ignore this additive response field.

Optional POST `requestId` (16–128 URL-safe alphanumeric/underscore/hyphen characters)
provides idempotent external admission. IDs are scoped to the external account and
bound to the complete JSON payload. Matching repeats return the same reservation
status, conflicting parameters return 409. A claim with uncertain admission also
returns 409 and must be reconciled, not retried using a new ID. Retention is seven
days; clients must not reuse expired IDs. Requests without `requestId` retain their
existing behavior. Failed authentication/admission validation occurs before claim.

## Ownership and rollout

The allocation Lua script checks resource ownership before consuming any queue.
Ownership has no TTL; verified cleanup releases only the matching owner atomically.
Lost start responses, interrupted initialization, missing metadata and uncertain
cleanup keep ownership and mark `reconciliation_required`. Restart and cancellation
do not release an unknown session. Malformed cleanup responses are not success.

Unassigned queue requests retain the existing one-hour expiry. Allocation skips
expired/terminal entries without recreating their hashes; it atomically makes an
assigned request's metadata/resource set persistent. The external account can
still poll/cancel its request from retained metadata after the legacy user-index
TTL expires. Verified release reinstates one-hour retention for terminal records;
quarantines remain persistent until explicit reconciliation. Missing cleanup state
is never interpreted as "already finished".

Cleanup accepts successful HTTP with an explicit finite numeric `should_finish`,
or the supported LDL/WebLabLib `{"message":"Deleted"}` acknowledgement. Positive
values require more waiting; negative values acknowledge completion. HTTP errors,
error flags, malformed data, `Not found` and `Unknown op` are uncertain, not
completion. This validates the library's acknowledgement, not the physical quality
of a lab's own disposal hook. Hardware restoration must still be qualified for
each lab. Legacy servers reporting only `Not found` need review; do not bypass
quarantine merely to preserve their old apparent availability.

Upgrade web and workers coherently after draining existing requests. Do not mix
older schedulers that ignore retained owners with the new quarantine behavior.
Persist/backup scheduler Redis: total data loss cannot be reconciled automatically
with physical hardware and requires admission to stop until ownership is verified.

Quarantined resources need explicit operator investigation of the exact lab and
session before reconciliation. Never delete an owner merely because a queue is
waiting. There is deliberately no force-release endpoint in this change.

Tests include isolated Redis Lua allocation/status, scoped discovery, idempotent
admission, lost startup, malformed cleanup, restart cancellation and stale-owner
release. They use vendor-neutral fake resources, not live equipment.
