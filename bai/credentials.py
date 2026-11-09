"""User-operated hidden input; metadata-only responses."""
from __future__ import annotations

import getpass
import sys
import warnings

from .core import Error, KEY, identity, read_json, write_json, digest


def store(ws):
    path = ws.path('private/secrets.json')
    if not path.exists():
        return {'schema_version': 1, 'profiles': {}}
    value = read_json(path)
    if not isinstance(value, dict) or value.get('schema_version') != 1 or not isinstance(value.get('profiles'), dict):
        raise Error('Invalid credential store; restore it or repair it privately.')
    for profile, keys in value['profiles'].items():
        identity(profile)
        if not isinstance(keys, dict) or any(not KEY.fullmatch(k) or not isinstance(v, str) or not v or '\x00' in v or '\n' in v or '\r' in v for k, v in keys.items()):
            raise Error('Invalid credential store fields; inspect privately.')
    return value


def values(ws, profile, required):
    identity(profile)
    profiles = store(ws)['profiles']
    result = profiles.get(profile, {})
    missing = [key for key in required if not result.get(key)]
    if missing:
        raise Error('Missing credential keys in selected profile: ' + ', '.join(missing) + '. Run secrets setup in your terminal.')
    return {key: result[key] for key in required}


def listing(ws, profile):
    from .tooling import manifests
    identity(profile)
    configured = store(ws)['profiles'].get(profile, {})
    tools, warnings = manifests(ws)
    required = {}
    for tool in tools:
        for key, description in tool['secrets'].items():
            required.setdefault(key, {'description': description, 'tools': []})['tools'].append(tool['id'])
    return {'profile': profile, 'keys': [{'key': key, 'configured': key in configured,
              **required.get(key, {'description': 'User-managed key.', 'tools': []})}
             for key in sorted(set(configured) | set(required))], 'warnings': warnings}


def prompt(key):
    if not sys.stdin.isatty():
        raise Error('Run this command yourself in an interactive terminal. Secret input is never accepted through pipes or arguments.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            value = getpass.getpass(f'{key} (hidden): ')
    except (getpass.GetPassWarning, EOFError) as exc:
        raise Error('Hidden terminal input unavailable; no credential was saved.') from exc
    if not value or any(c in value for c in ('\x00', '\n', '\r')):
        raise Error('Credential must be nonempty single-line text without NUL.')
    return value


def setup(ws, profile, required, update=False):
    identity(profile)
    if any(not KEY.fullmatch(key) for key in required):
        raise Error('Credential keys must be environment variable names.')
    path = ws.path('private/secrets.json')
    before = digest(path) if path.exists() else None
    existing = store(ws)['profiles'].get(profile, {})
    pending = {key: prompt(key) for key in required if update or key not in existing}
    if not pending:
        return {'profile': profile, 'changed_keys': [], 'message': 'All required keys are already configured.'}
    with ws.lock():
        if (digest(path) if path.exists() else None) != before:
            raise Error('Credential store changed while prompting; retry without overwriting concurrent changes.')
        state = store(ws)
        state['profiles'].setdefault(profile, {}).update(pending)
        write_json(path, state)
    return {'profile': profile, 'changed_keys': sorted(pending)}


def remove(ws, profile, key, yes=False):
    identity(profile)
    if not KEY.fullmatch(key):
        raise Error('Invalid credential key name.')
    affected = next((x['tools'] for x in listing(ws, profile)['keys'] if x['key'] == key), [])
    if not yes:
        if not sys.stdin.isatty():
            raise Error('Removal needs --yes or confirmation in your terminal.')
        print(f'Remove {profile}/{key}? Affected tools: {", ".join(affected) or "none registered"}', file=sys.stderr)
        if input('Type remove to continue: ') != 'remove':
            return {'removed': False, 'profile': profile, 'key': key}
    with ws.lock():
        state = store(ws)
        keys = state['profiles'].get(profile, {})
        existed = key in keys
        keys.pop(key, None)
        write_json(ws.path('private/secrets.json'), state)
    return {'removed': existed, 'profile': profile, 'key': key, 'affected_tools': affected}
