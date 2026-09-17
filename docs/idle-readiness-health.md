# Idle-only readiness checks

`json-success` checks may opt in to `idle_only: true` when an endpoint reports
not-ready during legitimate initialization, use, or cleanup. For example, PD-LS
reports failure while restoring the default FPGA bitstream. Caching that sample
as idle hardware failure can incorrectly reject the next reservation.

```yaml
healthchecks:
  readiness:
    type: json-success
    url: http://backend/healthcheck
    timeout: 5
    idle_only: true
```

If any configured JSON check opts in, the resource's **entire composite health
sample** is taken only while unassigned. Checks are not selectively omitted and
the remaining checks are not mistaken for a full healthy result. Existing health
and its original timestamp remain unchanged while assigned or quarantined.

Redis WATCH guards the ownership key across the HTTP checks and final atomic
health write. An assignment change, including an acquire/release cycle, discards
the sample. Real idle failures still mark the resource broken. The default is
false; existing configurations and robot-only checkers retain their behavior.

This is not ownership or cleanup enforcement. Persistent ownership, failed-cleanup
quarantine, and backend cleanup confirmation still govern admission. This option
does not make failed cleanup healthy, remove owners, or shorten cleanup. An idle
sample is retried on the regular health-check interval (currently30 seconds).
