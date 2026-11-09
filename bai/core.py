"""Local storage, portable records and optimistic updates."""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import tempfile

SCHEMA = 1
STATES = {'active', 'waiting', 'complete', 'cancelled', 'superseded', 'unknown'}
KINDS = {'learning', 'system', 'procedure', 'preference'}
PROVENANCE = {'user-stated', 'source-verified', 'runtime-observed', 'inference', 'historical-record'}
ID = re.compile(r'[a-z0-9][a-z0-9-]{0,95}\Z')
KEY = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\Z')
PRIVATE_NAMES = re.compile(r'(?i)(^\.env($|\.)|secret|credential|^id_(rsa|ed25519)$|\.pem$|\.key$)')
SECRET_LINE = re.compile(r'(?i)(authorization\s*[:=]|bearer\s+\S+|(?:password|secret|token|api[_-]?key)\s*["\x27]?\s*[:=]\s*\S+)')


class Error(Exception):
    """Safe, actionable diagnostic with no configuration values."""


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='microseconds')


def identity(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise Error('Use an identifier of lowercase letters, digits and hyphens (max 96).')
    return value


def relative(value):
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        raise Error('Expected a portable relative path using forward slashes.')
    p = PurePosixPath(value)
    if p.is_absolute() or PureWindowsPath(value).drive or any(x in ('', '.', '..') for x in value.split('/')):
        raise Error('Absolute paths and traversal are not allowed here.')
    if any(':' in x or x.endswith((' ', '.')) for x in p.parts):
        raise Error('Path is not portable to Windows.')
    reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
    if any(x.split('.')[0].upper() in reserved for x in p.parts):
        raise Error('Reserved Windows path component.')
    return p


def contained(root, value):
    p = relative(value)
    root = Path(root)
    if root.is_symlink():
        raise Error('Workspace paths cannot be symlinks.')
    target = root.joinpath(*p.parts)
    for part in [target, *target.parents]:
        if part == root:
            break
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise Error('Workspace paths cannot follow symlinks or junctions.')
    if not target.resolve().is_relative_to(root.resolve()):
        raise Error('Path leaves its registered root.')
    return target


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise Error(f'Cannot read valid JSON: {Path(path).name}') from exc


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8')


def atomic(path, data):
    path = Path(path)
    if path.is_symlink():
        raise Error('Refusing to replace a symlink.')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(prefix='.bai-tmp-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def write_json(path, value):
    atomic(path, encoded(value))


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def clean(text):
    text = '\n'.join(line for line in str(text).splitlines() if not SECRET_LINE.search(line))
    return re.sub(r'(https?://[^\s?]+)\?[^\s)]+', r'\1', text)


def strings(record, key):
    value = record.get(key)
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        raise Error(f'Expected a list of strings: {key}')
    return value


def validate_record(record, kind):
    if not isinstance(record, dict) or record.get('schema_version') != SCHEMA:
        raise Error('Unsupported record schema; expected schema_version 1.')
    identity(record.get('id'))
    if not isinstance(record.get('revision'), int) or record['revision'] < 1:
        raise Error('Record revision must be positive.')
    for key in ('title', 'summary', 'updated'):
        if not isinstance(record.get(key), str) or not record[key].strip():
            raise Error(f'Record needs nonempty {key}.')
    for key in ('repos', 'aliases'):
        strings(record, key)
    for repo in record['repos']:
        identity(repo)
    if 'historical' in record and not isinstance(record['historical'], bool):
        raise Error('historical must be a boolean.')
    if 'origin' in record and not isinstance(record['origin'], dict):
        raise Error('origin must be a provenance object.')
    if kind == 'tasks':
        if record.get('status') not in STATES:
            raise Error('Unknown task status.')
        for key in ('scope', 'verification', 'deployment', 'next_step', 'reuse_decision'):
            if not isinstance(record.get(key), str):
                raise Error(f'Task needs text field {key}.')
        for key in ('tools', 'learnings', 'evidence'):
            strings(record, key)
        if 'related_tasks' in record:
            for task in strings(record, 'related_tasks'):
                identity(task)
    else:
        if record.get('kind') not in KINDS or record.get('provenance') not in PROVENANCE:
            raise Error('Unknown memory kind or provenance.')
        if record.get('status') not in {'current', 'superseded', 'uncertain'}:
            raise Error('Unknown memory status.')
        for key in ('links', 'supersedes', 'sources'):
            strings(record, key)
        if not isinstance(record.get('relationships'), list):
            raise Error('Memory relationships must be a list.')
        for edge in record['relationships']:
            if not isinstance(edge, dict) or not all(isinstance(edge.get(k), str) and edge[k] for k in ('from', 'to', 'relation')):
                raise Error('Relationships need from, to and relation strings.')
            if edge['from'] not in record['repos'] or edge['to'] not in record['repos']:
                raise Error('Relationship endpoints must be listed in memory repos.')
        for link in record['links'] + record['supersedes']:
            identity(link)
    return record


class Workspace:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.data = contained(self.root, '.bai')

    def path(self, name):
        return contained(self.data, name)

    def require(self):
        cfg = read_json(self.path('workspace.json'))
        if not isinstance(cfg, dict) or cfg.get('schema_version') != SCHEMA or not isinstance(cfg.get('repositories'), dict):
            raise Error('Unsupported workspace version.')
        for name, repo in cfg['repositories'].items():
            identity(name)
            if not isinstance(repo, dict) or not isinstance(repo.get('summary'), str) or not isinstance(repo.get('registered'), str):
                raise Error('Invalid repository metadata; repair workspace.json.')
            strings(repo, 'aliases')
        return cfg

    def init(self):
        self.root.mkdir(parents=True, exist_ok=True)
        if self.data.exists():
            self.require()
            return {'initialized': False, 'workspace': str(self.data)}
        self.data.mkdir(mode=0o700)
        for name in ('tasks', 'knowledge', 'repos', 'tools', 'private', 'cache', 'artifacts', 'history'):
            self.path(name).mkdir(mode=0o700)
        write_json(self.path('workspace.json'), {'schema_version': SCHEMA, 'created': now(), 'repositories': {}})
        write_json(self.path('bindings.json'), {'schema_version': SCHEMA, 'paths': {}})
        atomic(self.path('preferences.md'), b'# Personal working preferences\n\nAdd durable working agreements here, without credentials.\n')
        return {'initialized': True, 'workspace': str(self.data)}

    @contextlib.contextmanager
    def lock(self):
        self.require()
        path = self.path('write.lock')
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise Error('Workspace is busy. If a prior process crashed, verify it stopped before removing .bai/write.lock.') from exc
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(str(os.getpid()))
            yield
        finally:
            path.unlink(missing_ok=True)

    def repo_path(self, repo):
        if repo not in self.require()['repositories']:
            raise Error(f'Unknown repository: {repo}')
        value = read_json(self.path('bindings.json'))['paths'].get(repo)
        if not value or not Path(value).is_dir():
            raise Error(f'Repository unavailable; use repo rebind: {repo}')
        return Path(value).resolve()

    def register_repo(self, repo, path, summary='', aliases=(), rebind=False):
        identity(repo)
        target = Path(path).expanduser().resolve()
        if not target.is_dir():
            raise Error('Repository path must be an existing directory.')
        with self.lock():
            cfg = self.require()
            if rebind:
                if repo not in cfg['repositories']:
                    raise Error('Cannot rebind an unregistered repository.')
            else:
                if repo in cfg['repositories']:
                    raise Error('Repository already registered; use rebind to change its location.')
                if self.path(f'repos/{repo}.md').exists():
                    raise Error('Repository brief already exists; reconcile it before registering.')
                cfg['repositories'][repo] = {'summary': summary or 'Repository brief needs review.', 'aliases': list(aliases), 'registered': now()}
                atomic(self.path(f'repos/{repo}.md'), f'# {repo}\n\n{summary}\n\n## Entry points and relationships\n\nReview source and describe how this repo participates in system maps.\n'.encode())
                write_json(self.path('workspace.json'), cfg)
            bindings = read_json(self.path('bindings.json'))
            bindings['paths'][repo] = str(target)
            write_json(self.path('bindings.json'), bindings)
        return {'repo': repo, 'path': str(target), 'memory_review': 'Review related system maps after registering or rebinding.'}

    def resolve_repo(self, value):
        if not value:
            return None
        cfg = self.require()
        for repo, data in cfg['repositories'].items():
            if value.casefold() in [repo.casefold(), *(x.casefold() for x in data['aliases'])]:
                return repo
        paths = read_json(self.path('bindings.json'))['paths']
        candidate = Path(value).expanduser().resolve()
        matches = [(len(p), name) for name, p in paths.items() if candidate.is_relative_to(Path(p))]
        return max(matches)[1] if matches else None

    def record_path(self, kind, name):
        identity(name)
        if kind not in ('tasks', 'knowledge'):
            raise Error('Unknown record collection.')
        return self.path(f'{kind}/{name}.json')

    def record(self, kind, name):
        record = validate_record(read_json(self.record_path(kind, name)), kind)
        if record['id'] != name:
            raise Error('Record filename and identity disagree.')
        return record

    def records(self, kind):
        records, warnings = [], []
        for path in sorted(self.path(kind).glob('*.json')):
            try:
                records.append(self.record(kind, path.stem))
            except Error:
                warnings.append(f'Invalid {kind} record: {path.name}; run doctor and repair it.')
        return records, warnings

    def create(self, kind, name, title, repos=(), memory_kind='learning', provenance='user-stated'):
        identity(name)
        with self.lock():
            path = self.record_path(kind, name)
            if path.exists() or self.path(f'{kind}/{name}.md').exists():
                raise Error('Record or narrative already exists; reconcile it before creating another.')
            cfg = self.require()
            if any(r not in cfg['repositories'] for r in repos):
                raise Error('Register repositories before referencing them.')
            record = {'schema_version': SCHEMA, 'id': name, 'title': title, 'revision': 1, 'updated': now(),
                      'summary': 'Awaiting review.', 'repos': list(repos), 'aliases': [], 'reviewed': {}, 'historical': False, 'origin': {}}
            if kind == 'tasks':
                record.update(status='active', scope='', verification='Not verified.', deployment='Not performed.',
                              next_step='Establish scope and inspect current evidence.', reuse_decision='', tools=[], learnings=[], evidence=[], related_tasks=[])
            else:
                record.update(kind=memory_kind, provenance=provenance, status='uncertain', sources=[], links=[], supersedes=[], relationships=[])
            validate_record(record, kind)
            atomic(self.path(f'{kind}/{name}.md'), f'# {title}\n\n## Current understanding\n\n## Decisions and evidence\n\n## Open questions and next step\n'.encode())
            write_json(path, record)
        return record

    def update(self, kind, name, patch, expected):
        if not isinstance(patch, dict) or set(patch) & {'id', 'revision', 'schema_version', 'updated', 'reviewed'}:
            raise Error('Update must be an object without identity, revision or review fields.')
        with self.lock():
            old = self.record(kind, name)
            if old['revision'] != expected:
                raise Error('Revision changed; reread the record and reconcile your update.')
            if set(patch) - set(old):
                raise Error('Update contains unknown record fields.')
            record = {**old, **patch, 'revision': expected + 1, 'updated': now()}
            validate_record(record, kind)
            if any(r not in self.require()['repositories'] for r in record['repos']):
                raise Error('Record references an unregistered repository.')
            history = self.path(f'history/{kind}-{name}-r{expected}.json')
            if history.exists():
                raise Error('Revision history already exists; repair the revision before updating.')
            write_json(history, old)
            write_json(self.record_path(kind, name), record)
        return record
