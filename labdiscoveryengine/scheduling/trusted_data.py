"""Generic, opt-in external server data; never browser-authoritative data.

LDE transports opaque application envelopes, not their signing keys or policy.
Admission and dispatch both check account/laboratory/key scope. Scheduler-owned
identity and timing fields can never be supplied through this channel.
"""
import json
import re

from labdiscoveryengine.utils import lde_config


class TrustedDataError(ValueError):
    """Safe, payload-free validation error."""


def validate_server_data(data, username, laboratory, resources, role='external', config=None):
    config = lde_config if config is None else config
    variables = getattr(config, 'variables', {})
    data = {} if data is None else data
    if not isinstance(data, dict):
        raise TrustedDataError('serverInitialData must be an object')
    required_policy = variables.get('REQUIRED_SERVER_INITIAL_DATA_KEYS', {})
    allowed_policy = variables.get('EXTERNAL_SERVER_INITIAL_DATA_KEYS', {})
    if not isinstance(required_policy, dict) or not isinstance(allowed_policy, dict):
        raise TrustedDataError('Invalid server data policy')
    required = required_policy.get(laboratory, [])
    if not isinstance(required, list) or not all(isinstance(key, str) for key in required):
        raise TrustedDataError('Invalid server data policy')
    if not data and not required:
        return {}
    user = config.external_users.get(username)
    if role != 'external' or user is None or laboratory not in user.laboratories:
        raise TrustedDataError('This laboratory requires authorized external server data')
    account_policy = allowed_policy.get(username, {})
    if not isinstance(account_policy, dict):
        raise TrustedDataError('Invalid server data policy')
    allowed = account_policy.get(laboratory, [])
    if not isinstance(allowed, list) or not all(isinstance(key, str) for key in allowed):
        raise TrustedDataError('Invalid server data policy')
    if (not set(data).issubset(allowed) or not set(required).issubset(data)
            or any(not isinstance(key, str) or len(key) > 128
                   or not re.fullmatch(r'[a-z][a-z0-9_-]*\.[a-zA-Z0-9_.-]+', key)
                   or key.startswith(('request.', 'priority.', 'reservation.', 'lde.')) for key in data)):
        raise TrustedDataError('Server data keys are missing, protected or not permitted')
    lab = config.laboratories[laboratory]
    if not resources or any(name not in lab.resources or name not in config.resources
                            or config.resources[name].api != 'weblablib-v1.0' for name in resources):
        raise TrustedDataError('Server data requires supported, scoped resources')
    # Bound depth/node count before encoding; error messages never include data.
    pending = [(data, 0)]
    nodes = 0
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if depth > 8 or nodes > 256:
            raise TrustedDataError('Server data exceeds structural limits')
        if isinstance(value, dict):
            if len(value) > 256:
                raise TrustedDataError('Server data exceeds structural limits')
            if any(not isinstance(k, str) or len(k) > 128 for k in value):
                raise TrustedDataError('Invalid server data keys')
            pending.extend((v, depth + 1) for v in value.values())
        elif isinstance(value, list):
            if len(value) > 256:
                raise TrustedDataError('Server data exceeds structural limits')
            pending.extend((v, depth + 1) for v in value)
        elif isinstance(value, str) and len(value) > 8192:
            raise TrustedDataError('Server data exceeds size limit')
    try:
        encoded = json.dumps(data, allow_nan=False, separators=(',', ':'))
        if len(encoded.encode()) > 8192:
            raise TrustedDataError('Server data exceeds size limit')
        return json.loads(encoded)
    except (TypeError, ValueError, RecursionError):
        raise TrustedDataError('Invalid or oversized server data') from None


def validate_request(request, resource=None, config=None):
    resources = request.resources if resource is None else [resource.identifier]
    if resource is not None and request.server_initial_data and resource.identifier not in request.resources:
        raise TrustedDataError('Dispatch resource is outside the admitted request')
    return validate_server_data(request.server_initial_data, request.user_identifier,
                                request.laboratory, resources, request.user_role, config=config)
