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
import time

SCHEMA = 1
UNFINISHED_STATES = {'active', 'waiting', 'unknown'}
STATES = UNFINISHED_STATES | {'complete', 'cancelled', 'superseded'}
WAITING_ON = ('user', 'external', 'unknown')
KINDS = {'learning', 'system', 'procedure', 'preference'}
PROVENANCE = {'user-stated', 'source-verified', 'runtime-observed', 'inference', 'historical-record'}
ID = re.compile(r'[a-z0-9][a-z0-9-]{0,95}\Z')
KEY = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\Z')
PRIVATE_NAMES = re.compile(r'(?i)(^\.env($|\.)|secret|credential|^id_(rsa|ed25519)$|\.pem$|\.key$)')
SECRET_LINE = re.compile(r'(?i)(authorization\s*[:=]|bearer\s+\S+|(?:password|secret|token|api[_-]?key)\s*["\x27]?\s*[:=]\s*\S+)')


class Error(Exception):
    """Safe, actionable diagnostic with no configuration values."""


class OpenSetChanged(Error):
    def __init__(self, topic, tasks, token):
        super().__init__('The open task set changed. Review the current candidates before creating another task.')
        latest = sorted(tasks, key=lambda task: (task['updated'], task['id']), reverse=True)[:5]
        self.details = {'topic': topic, 'open_set': token, 'candidate_count': len(tasks), 'candidates': [
            {'id': task['id'], 'title': clean(task['title'])[:160], 'revision': task['revision'],
             'done_when': clean(task.get('done_when', ''))[:160]} for task in latest
        ]}


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


def open_tasks(records, topic=None):
    return [r for r in records if r['status'] in UNFINISHED_STATES and not r.get('historical')
            and (topic is None or r.get('topic') == topic)]


def validate_record(record, kind):
    if not isinstance(record, dict) or record.get('schema_version') != SCHEMA:
        raise Error('Unsupported record schema; expected schema_version 1.')
    identity(record.get('id'))
    if type(record.get('revision')) is not int or record['revision'] < 1:
        raise Error('Record revision must be positive.')
    if kind == 'topics':
        for key in ('title', 'purpose', 'updated'):
            if not isinstance(record.get(key), str) or not record[key].strip():
                raise Error(f'Topic needs nonempty {key}.')
        if any(not alias.strip() for alias in strings(record, 'aliases')):
            raise Error('Topic aliases must be nonempty.')
        for key in ('reference_repos', 'implementation_repos', 'requirements'):
            for ref in strings(record, key):
                identity(ref)
        return record
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
        if record.get('topic') is not None:
            identity(record['topic'])
        if 'done_when' in record and not isinstance(record['done_when'], str):
            raise Error('Task done_when must be text.')
        if record.get('waiting_on', 'unknown') not in WAITING_ON:
            raise Error('Task waiting_on must be user, external or unknown.')
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
        if not isinstance(cfg.get('topics', {}), dict):
            raise Error('Invalid topic registry; repair workspace.json.')
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
    def lock(self, wait=0):
        self.require()
        path = self.path('write.lock')
        deadline = time.monotonic() + wait
        while True:
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                break
            except FileExistsError as exc:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise Error('Workspace is busy. Retry shortly. If a prior process crashed, verify it stopped before removing .bai/write.lock.') from exc
                time.sleep(min(0.025, remaining))
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
        value = os.fspath(value)
        repositories = self.require()['repositories']
        folded = value.casefold()
        if folded in repositories:
            return folded
        aliases = [repo for repo, data in repositories.items()
                   if folded in {alias.casefold() for alias in data['aliases']}]
        if len(aliases) > 1:
            raise Error('Repository alias is ambiguous. Choose a repository ID from: ' + ', '.join(sorted(aliases)))
        if aliases:
            return aliases[0]
        if not ('/' in value or '\\' in value or value in ('.', '..', '~') or PureWindowsPath(value).drive):
            return None
        paths = read_json(self.path('bindings.json'))['paths']
        candidate = Path(value).expanduser().resolve()
        matches = [(len(Path(path).parts), name) for name, path in paths.items()
                   if name in repositories and candidate.is_relative_to(Path(path))]
        if not matches:
            return None
        depth = max(length for length, _ in matches)
        deepest = sorted(name for length, name in matches if length == depth)
        if len(deepest) > 1:
            raise Error('Repository path is ambiguous. Choose a repository ID from: ' + ', '.join(deepest))
        return deepest[0]

    def resolve_topic(self, value):
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise Error('Use a topic ID or alias.')
        folded = value.strip().casefold()
        topics = self.require().get('topics', {})
        if folded in topics:
            return self.record('topics', folded)['id']
        records, _ = self.records('topics')
        matches = [r['id'] for r in records if folded in {alias.strip().casefold() for alias in r['aliases']}]
        if len(matches) > 1:
            raise Error('Topic alias is ambiguous. Choose a topic ID from: ' + ', '.join(sorted(matches)))
        return matches[0] if matches else None

    def topic_id(self, value):
        name = self.resolve_topic(value)
        if name is None:
            raise Error('Topic is not registered. Use topic list to find a topic ID.')
        return name

    @staticmethod
    def open_set(records, topic=None):
        tasks = sorted((r['id'], r['revision']) for r in open_tasks(records, topic))
        return hashlib.sha256(encoded([topic, tasks])).hexdigest()

    def record_path(self, kind, name):
        identity(name)
        if kind not in ('tasks', 'knowledge'):
            raise Error('Unknown record collection.')
        return self.path(f'{kind}/{name}.json')

    def record(self, kind, name):
        if kind == 'topics':
            identity(name)
            raw = self.require().get('topics', {}).get(name)
            if raw is None:
                raise Error('Topic is not registered.')
        else:
            raw = read_json(self.record_path(kind, name))
        record = validate_record(raw, kind)
        if record['id'] != name:
            raise Error('Topic key and identity disagree.' if kind == 'topics' else 'Record filename and identity disagree.')
        return record

    def records(self, kind):
        records, warnings = [], []
        names = (sorted(self.require().get('topics', {})) if kind == 'topics'
                 else [path.stem for path in sorted(self.path(kind).glob('*.json'))])
        for name in names:
            try:
                records.append(self.record(kind, name))
            except Error:
                label = name if kind == 'topics' else f'{name}.json'
                warnings.append(f'Invalid {kind} record: {label}; run doctor and repair it.')
        return records, warnings

    def create_topic(self, name, title, purpose, aliases=(), reference_repos=(), implementation_repos=(), requirements=()):
        identity(name)
        with self.lock():
            cfg = self.require()
            topics = cfg.setdefault('topics', {})
            if name in topics:
                raise Error('Topic already registered. Use topic update to revise it.')
            record = {'schema_version': SCHEMA, 'id': name, 'revision': 1, 'updated': now(),
                      'title': title, 'purpose': purpose, 'aliases': list(aliases), 'requirements': list(requirements),
                      'reference_repos': list(reference_repos), 'implementation_repos': list(implementation_repos)}
            self.validate_topic(record)
            topics[name] = record
            write_json(self.path('workspace.json'), cfg)
        return record

    def validate_topic(self, record):
        validate_record(record, 'topics')
        repositories = self.require()['repositories']
        if any(repo not in repositories for repo in record['reference_repos'] + record['implementation_repos']):
            raise Error('Register repositories before referencing them in a topic.')
        for name in record['requirements']:
            self.record('knowledge', name)

    def create(self, kind, name, title, repos=(), memory_kind='learning', provenance='user-stated', *,
               topic=None, done_when=None, waiting_on=None, aliases=(), expected_open_set=None):
        identity(name)
        guarded = kind == 'tasks' and expected_open_set is not None
        if kind == 'tasks' and topic is not None and not guarded:
            raise Error('Topic-bound task creation needs --expected-open-set from topic show or context --brief.')
        with self.lock(wait=1 if guarded else 0):
            if kind == 'tasks':
                if topic is not None:
                    topic = self.topic_id(topic)
                if guarded:
                    tasks, _ = self.records('tasks')
                    token = self.open_set(tasks, topic)
                    if expected_open_set != token:
                        raise OpenSetChanged(topic, open_tasks(tasks, topic), token)
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
                record['aliases'] = list(aliases)
                for key, value in (('topic', topic), ('done_when', done_when), ('waiting_on', waiting_on)):
                    if value is not None:
                        record[key] = value
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
            if kind == 'topics':
                name = self.topic_id(name)
            old = self.record(kind, name)
            if old['revision'] != expected:
                raise Error('Revision changed; reread the record and reconcile your update.')
            optional = {'historical', 'origin'} if kind != 'topics' else set()
            if kind == 'tasks':
                optional.update({'related_tasks', 'topic', 'done_when', 'waiting_on'})
            if set(patch) - set(old) - optional:
                raise Error('Update contains unknown record fields.')
            record = {**old, **patch, 'revision': expected + 1, 'updated': now()}
            if kind == 'topics':
                self.validate_topic(record)
            else:
                if kind == 'tasks' and record.get('topic') is not None:
                    record['topic'] = self.topic_id(record['topic'])
                validate_record(record, kind)
                if any(r not in self.require()['repositories'] for r in record['repos']):
                    raise Error('Record references an unregistered repository.')
            self.commit_revision(kind, old, record)
        return record

    def commit_revision(self, kind, old, record):
        """Persist a record revision while the caller holds the workspace lock."""
        name = old['id']
        history = self.path(f'history/{kind}-{name}-r{old["revision"]}.json')
        if history.exists():
            if read_json(history) != old:
                raise Error('Revision history conflicts with the current record; reconcile it before updating.')
        else:
            write_json(history, old)
        if kind == 'topics':
            cfg = self.require()
            cfg['topics'][name] = record
            write_json(self.path('workspace.json'), cfg)
        else:
            write_json(self.record_path(kind, name), record)
