Trusted external server data
============================

The external reservation API optionally accepts ``serverInitialData``: a small
JSON object sent by an authenticated external service, not by a browser. This
channel is disabled by default. Ordinary reservations remain unchanged.

Enable only reviewed account/laboratory/key combinations in ``configuration.yml``::

    EXTERNAL_SERVER_INITIAL_DATA_KEYS:
      trusted_provider:
        protected_lab: [example.authorization]
    REQUIRED_SERVER_INITIAL_DATA_KEYS:
      protected_lab: [example.authorization]

The external account must also have the laboratory in its existing credential
scope. Required keys prevent an empty request or native LDE login from silently
starting a protected lab in an ordinary mode. Native users, including LDE
administrators, cannot supply this external context. A separate ordinary logical
lab can share the same physical resource IDs; never duplicate physical resources
to represent access modes.

The POST body keeps the two channels separate::

    {
      "laboratory": "protected_lab",
      "resources": ["board-1"],
      "userIdentifier": "external-user",
      "backUrl": "https://provider.example/",
      "requestId": "unique-request-0123456789",
      "clientInitialData": {"view": "ordinary-ui-setting"},
      "serverInitialData": {"example.authorization": {"token": "opaque-value"}}
    }

LDE does not issue or interpret application tokens. Their signing keys,
authorization policy and final validation belong to the external service and
laboratory implementation. Allowlisting a key delegates authority to its sender;
it is not validation of the envelope's contents.

Only ``weblablib-v1.0`` resources currently support this channel. Admission and
dispatch revalidate account, lab, resource and key scope. Keys must be namespaced;
``request.*``, ``priority.*``, ``reservation.*`` and ``lde.*`` are reserved.
Scheduler identity and timing fields cannot be overwritten. Data is limited to
8 KiB of encoded JSON, eight nesting levels and 256 visited values; non-finite
numbers are rejected.

The payload is retained in private Redis reservation metadata and forwarded in
the laboratory's ``server_initial_data``. It is not included in status responses,
discovery, browser initial data or audit records. Treat Redis, its durable files,
backups, the service account and southbound credentials as sensitive. Use TLS for
off-host connections. The laboratory must also avoid reflecting or logging the
payload. This feature does not secure an otherwise untrusted Redis deployment.

Use the optional idempotent ``requestId`` contract and preserve an uncertain
admission's identity. Reissuing a differently signed envelope with the same ID
is a conflicting request, not a new authorization. Revoking permission while a
request is queued refuses dispatch; conservative lifecycle handling may retain
ownership for operator reconciliation. It never grants a fallback lab session.

Roll out compatible web and worker processes together, with admissions drained.
Do not run an old scheduler beside one using retained ownership/quarantine rules.
See :doc:`redis_durability` for storage and recovery requirements.

Passive controller health
-------------------------

A healthy camera/programmer does not prove the control worker is functional.
For a passive controller endpoint returning ``{"success": true}``, explicitly
configure a scheduler check of type ``json-success``::

    healthchecks:
      controller:
        type: json-success
        url: http://controller.example/instances/1
        timeout: 5

The periodic checker requires HTTP 200 and the boolean ``true``; timeouts,
redirects and malformed/negative responses mark the resource broken for new
admission. Any negative check overrides positive ones. Existing ``http`` entries
remain descriptive links, not new scheduler probes, and existing Robotchecker
behavior is unchanged. Configure only passive status reads, never reset/program
endpoints. As with other periodic health checks, status is a recent observation,
not a reservation or a guarantee against a fault after the check.
