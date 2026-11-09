"""Reusable tool discovery and bounded, redacted subprocess execution."""
from __future__ import annotations

import os
import contextlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import quote, quote_plus

from .core import Error, KEY, contained, identity, read_json, relative, strings, write_json

PROJECT = Path(__file__).resolve().parents[1]
EFFECTS = {'read-only', 'local-write', 'external-write'}
MAX_OUTPUT = 1024 * 1024


def validate(tool):
    if not isinstance(tool, dict) or tool.get('schema_version') != 1:
        raise Error('Tool manifest needs schema_version 1.')
    identity(tool.get('id'))
    for key in ('description', 'inputs', 'outputs', 'verification', 'recovery'):
        if not isinstance(tool.get(key), str) or not tool[key].strip():
            raise Error(f'Tool manifest needs {key}.')
    relative(tool.get('entry'))
    if tool.get('runtime') not in {'python', 'executable', 'command'} or tool.get('effects') not in EFFECTS:
        raise Error('Tool needs a supported runtime and side-effect classification.')
    if tool.get('cwd'):
        relative(tool['cwd'])
    if tool.get('runtime') == 'command' and '/' in tool['entry']:
        raise Error('Command runtime entry must be a program name resolved through PATH.')
    for key in ('keywords', 'arguments', 'dependencies'):
        strings(tool, key)
    if tool.get('repo'):
        identity(tool['repo'])
    if tool.get('replaced_by'):
        identity(tool['replaced_by'])
    secrets = tool.get('secrets')
    if not isinstance(secrets, dict) or any(not KEY.fullmatch(k) or not isinstance(v, str) or not v for k, v in secrets.items()):
        raise Error('Tool secrets must map environment key names to descriptions, never values.')
    environment = tool.get('environment', {})
    if not isinstance(environment, dict) or any(not KEY.fullmatch(k) or not isinstance(v, str) for k, v in environment.items()):
        raise Error('Tool environment must map key names to non-secret string settings.')
    if set(environment) & set(secrets):
        raise Error('Runtime settings and credential keys must be distinct.')
    return tool


def manifests(ws):
    result, warnings, seen = [], [], set()
    for directory, base in [(PROJECT / 'examples/tools', PROJECT), (ws.path('tools'), ws.root)]:
        for path in sorted(directory.glob('*.json')):
            try:
                if path.is_symlink():
                    raise Error('Symlink manifest.')
                tool = validate(read_json(path))
                if tool['id'] in seen:
                    raise Error('Duplicate tool identifier.')
                seen.add(tool['id'])
                result.append({**tool, '_path': str(path.relative_to(base)), '_base': str(base)})
            except Error:
                warnings.append(f'Invalid or duplicate tool manifest: {path.name}')
    return result, warnings


def find(ws, query=''):
    from .memory import terms
    tools, warnings = manifests(ws)
    words = terms(query)
    tasks, task_warnings = ws.records('tasks')
    warnings.extend(task_warnings)
    ongoing = query.strip().lower() in {'ongoing', 'ongoing work', 'current work', 'active', 'active tasks'}
    matched_tasks = [t for t in tasks if not t.get('historical') and t['status'] in {'active', 'waiting'}
                     and (ongoing or words & terms(t['title'] + ' ' + ' '.join(t['aliases'])))]
    linked = {name for task in matched_tasks for name in task['tools']}
    ranked = sorted(tools, key=lambda t: (-len(words & terms(t['id'] + ' ' + t['description'] + ' ' + ' '.join(t['keywords']))), t['id']))
    ranked.sort(key=lambda t: t['id'] not in linked)
    return {'tools': [{**{k: v for k, v in t.items() if k != '_base'},
                       'matched_tasks': [task['id'] for task in matched_tasks if t['id'] in task['tools']]}
                      for t in ranked if t['id'] in linked or (not ongoing and (not words or words & terms(t['id'] + ' ' + t['description'] + ' ' + ' '.join(t['keywords']))))], 'warnings': warnings}


def get(ws, name):
    identity(name)
    tools, warnings = manifests(ws)
    if warnings:
        raise Error('Repair invalid or duplicate tool manifests before executing tools; run tools find.')
    for tool in tools:
        if tool['id'] == name:
            return tool
    raise Error('Tool is not registered; search tools before creating another.')


def register(ws, manifest, replace=False):
    if any(str(k).startswith('_') for k in manifest):
        raise Error('Internal tool fields cannot be registered.')
    validate(manifest)
    with ws.lock():
        tools, _ = manifests(ws)
        current = next((x for x in tools if x['id'] == manifest['id']), None)
        if current and (not replace or current['_base'] != str(ws.root) or not current['_path'].startswith('.bai/')):
            raise Error('Tool already exists. Use --replace for a reviewed local manifest update; built-ins cannot be shadowed.')
        if manifest.get('repo') and manifest['repo'] not in ws.require()['repositories']:
            raise Error('Register the tool repository first.')
        write_json(ws.path(f'tools/{manifest["id"]}.json'), manifest)
    return {'registered': manifest['id'], 'note': 'Run tools show and its verification command before live use.'}


def entry_path(ws, tool):
    base = ws.repo_path(tool['repo']) if tool.get('repo') else Path(tool['_base'])
    cwd = contained(base, tool['cwd']) if tool.get('cwd') else base
    if not cwd.is_dir():
        raise Error('Tool working directory is missing.')
    if tool['runtime'] == 'command':
        executable = shutil.which(tool['entry'])
        if not executable:
            raise Error('Tool command is unavailable on PATH.')
        path = Path(executable)
    else:
        path = contained(base, tool['entry'])
    if not path.is_file():
        raise Error('Tool entry point is missing; inspect tools show before running.')
    return cwd, path


def inspect_tool(ws, tool):
    warnings = []
    try:
        entry_path(ws, tool)
    except Error:
        warnings.append(f'Tool entry point or repository unavailable: {tool["id"]}')
    for name in tool['dependencies']:
        if not shutil.which(name):
            warnings.append(f'Tool {tool["id"]} needs executable {name}.')
    if tool.get('replaced_by'):
        warnings.append(f'Tool {tool["id"]} is retired; use {tool["replaced_by"]}.')
    return {**{k: v for k, v in tool.items() if k != '_base'}, 'warnings': warnings,
            'invocation': [sys.executable, str(PROJECT / 'bai.py'), '--root', str(ws.root), 'tools', 'run', tool['id'], '--', '<tool arguments>']}


def redact(text, secrets):
    candidates = {item for value in secrets for item in (value, quote(value, safe=''), quote_plus(value)) if item}
    for value in sorted(candidates, key=len, reverse=True):
        text = text.replace(value, '[REDACTED]')
    return text


def run(ws, name, args, profile, execute=False, timeout=300, stdin_file=None):
    from .credentials import values
    tool = get(ws, name)
    if tool.get('replaced_by'):
        raise Error(f'Tool is retired; use {tool["replaced_by"]}.')
    if tool['effects'] == 'external-write' and not execute:
        raise Error('This tool writes externally. Review task authorization and pass --execute explicitly.')
    if timeout <= 0:
        raise Error('Timeout must be positive.')
    details = inspect_tool(ws, tool)
    if details['warnings']:
        raise Error('Tool prerequisites unavailable; run tools show.')
    base, entry = entry_path(ws, tool)
    if tool['runtime'] == 'executable' and entry.suffix.lower() in {'.bat', '.cmd'}:
        raise Error('Batch files are not supported as tool entry points; use Python or a native executable.')
    secrets = values(ws, profile, tool['secrets']) if tool['secrets'] else {}
    command = ([sys.executable, str(entry)] if tool['runtime'] == 'python' else [str(entry)]) + tool['arguments'] + list(args)
    # Start from operational essentials, not the agent's credential-bearing environment.
    allowed = {'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP', 'TMPDIR', 'HOME', 'USERPROFILE',
               'APPDATA', 'LOCALAPPDATA', 'LANG', 'LC_ALL', 'TERM'}
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    env.update(tool.get('environment', {}))
    env.update(secrets)
    env['PYTHONIOENCODING'] = 'utf-8'
    # Anonymous temporary streams prevent pipe deadlock and avoid unbounded RAM use.
    # They are private, unlinked on close, and never exposed as workspace artifacts.
    with contextlib.ExitStack() as stack:
        stdout = stack.enter_context(tempfile.TemporaryFile())
        stderr = stack.enter_context(tempfile.TemporaryFile())
        stdin = stack.enter_context(Path(stdin_file).open('rb')) if stdin_file is not None else subprocess.DEVNULL
        try:
            proc = subprocess.Popen(command, cwd=base, env=env, stdin=stdin, stdout=stdout, stderr=stderr, shell=False)
        except OSError as exc:
            raise Error('Tool could not start; check its runtime and entry point.') from exc
        outcome = 'finished'
        try:
            code = proc.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            proc.kill()
            proc.wait()
            code, outcome = 124, 'interrupted; remote outcome may be unknown'
        outputs, truncated = {}, False
        for label, stream in [('stdout', stdout), ('stderr', stderr)]:
            stream.seek(0)
            # Read a tail beyond the display limit to avoid leaking a secret split at truncation.
            extra = max((len(v.encode('utf-8')) * 3 for v in secrets.values()), default=0)
            data = stream.read(MAX_OUTPUT + extra + 1)
            text = redact(data.decode('utf-8', errors='replace'), secrets.values())
            if len(data) > MAX_OUTPUT:
                truncated = True
                # Drop the last output line after redaction at a byte boundary.
                text = text[:MAX_OUTPUT].rsplit('\n', 1)[0] if '\n' in text[:MAX_OUTPUT] else '[Output exceeds display limit.]'
            outputs[label] = text
    return {'tool': name, 'exit_code': code, 'outcome': outcome, 'effects': tool['effects'], 'truncated': truncated,
            **outputs, 'recovery': tool['recovery'] if code else None}
