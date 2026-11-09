"""Bounded retrieval and explicit review of file-backed evidence."""
from __future__ import annotations

import hashlib
import json
import re
import shutil

from .core import Error, PRIVATE_NAMES, clean, contained, digest, encoded, now, read_json, write_json


def terms(text):
    stop = {'the', 'and', 'for', 'with', 'from', 'this', 'that', 'what', 'how', 'please', 'repo'}
    return set(re.findall(r'[a-z0-9]+', clean(text).lower())) - stop


def body(ws, kind, name):
    path = ws.path(f'{kind}/{name}.md')
    if not path.is_file():
        raise Error(f'Missing narrative: {kind}/{name}.md')
    return clean(path.read_text(encoding='utf-8'))


def metadata_hash(record):
    return hashlib.sha256(encoded({k: v for k, v in record.items() if k not in {'revision', 'updated', 'reviewed'}})).hexdigest()


def source_path(ws, ref):
    if ':' not in ref:
        raise Error('Sources use workspace:knowledge/name.md or repo-id:path/to/source.')
    repo, name = ref.split(':', 1)
    if any(PRIVATE_NAMES.search(part) or part == '.git' for part in name.split('/')):
        raise Error('Credential and Git-internal paths cannot be memory sources.')
    if repo == 'workspace':
        if not name.startswith(('tasks/', 'knowledge/', 'repos/')) and name != 'preferences.md':
            raise Error('Workspace memory sources must be narratives or repository briefs.')
        if not name.endswith('.md'):
            raise Error('Workspace memory sources must be Markdown.')
        return ws.path(name)
    return contained(ws.repo_path(repo), name)


def review_hashes(ws, kind, record):
    result = {'narrative': digest(ws.path(f'{kind}/{record["id"]}.md')), 'metadata': metadata_hash(record)}
    if kind == 'knowledge':
        for ref in record['sources']:
            path = source_path(ws, ref)
            if not path.is_file():
                raise Error('A cited memory source is unavailable.')
            result[ref] = digest(path)
    return result


def review(ws, repo=None):
    ws.require()
    records, warnings = ws.records('knowledge')
    ids = {r['id'] for r in records}
    findings = []
    for record in records:
        if repo and repo not in record['repos']:
            continue
        reasons = []
        if record['status'] == 'uncertain':
            reasons.append('Understanding is marked uncertain.')
        try:
            hashes = review_hashes(ws, 'knowledge', record)
            if hashes != record.get('reviewed'):
                reasons.append('Narrative, metadata or cited source changed since review (or has never been reviewed).')
        except (Error, OSError):
            reasons.append('A narrative or cited source is unavailable; inspect it before relying on this memory.')
        if any(x not in ids or x == record['id'] for x in record['links'] + record['supersedes']):
            reasons.append('Missing or self-referencing knowledge link.')
        if any(x not in ws.require()['repositories'] for x in record['repos']):
            reasons.append('Unregistered repository reference.')
        if record['kind'] == 'system' and not record['relationships']:
            reasons.append('System map needs explicit relationships and normal/alternate flow explanations.')
        if not record['sources']:
            reasons.append('Add a source, including a task narrative for user explanations.')
        if reasons:
            findings.append({'id': record['id'], 'reasons': reasons})
    # Candidate overlaps are review hints, never automatically resolved facts.
    current = [r for r in records if r['status'] == 'current' and (not repo or repo in r['repos'])]
    for i, first in enumerate(current):
        for second in current[i + 1:]:
            if set(first['aliases']) & set(second['aliases']) and not ({first['id']} & set(second['supersedes']) or {second['id']} & set(first['supersedes'])):
                findings.append({'id': first['id'], 'reasons': [f'Shared aliases with {second["id"]}; check for duplicate or conflicting claims.']})
    return {'findings': findings, 'warnings': warnings, 'note': 'Structural review only. The agent must reconcile meaning and evidence.'}


def inventory(ws):
    cfg = ws.require()
    entries, warnings = [], []
    for kind in ('tasks', 'knowledge'):
        records, problems = ws.records(kind)
        warnings.extend(problems)
        for r in records:
            try:
                narrative = body(ws, kind, r['id'])
            except (Error, OSError):
                narrative = ''
                warnings.append(f'Missing narrative for {kind}/{r["id"]}.')
            entries.append({'kind': 'task' if kind == 'tasks' else r['kind'], 'id': r['id'], 'title': clean(r['title']),
                            'summary': clean(r['summary'])[:500], 'repos': r['repos'], 'status': r['status'], 'updated': r['updated'],
                            'path': f'.bai/{kind}/{r["id"]}.json', 'narrative': f'.bai/{kind}/{r["id"]}.md',
                            'links': r.get('links', []), 'supersedes': r.get('supersedes', []),
                            'provenance': r.get('provenance'), 'reviewed': bool(r.get('reviewed')),
                            'terms': sorted(terms(r['title'] + ' ' + r['summary'] + ' ' + ' '.join(r['aliases']) + ' ' + narrative))})
    for repo, data in cfg['repositories'].items():
        try:
            text = ws.path(f'repos/{repo}.md').read_text(encoding='utf-8')
        except OSError:
            text = ''
            warnings.append(f'Missing repository brief: {repo}')
        entries.append({'kind': 'repo', 'id': repo, 'title': repo, 'summary': clean(data['summary'])[:500], 'repos': [repo],
                        'path': f'.bai/repos/{repo}.md', 'status': 'current', 'updated': data['registered'],
                        'terms': sorted(terms(repo + ' ' + data['summary'] + ' ' + ' '.join(data['aliases']) + ' ' + text))})
    from .tooling import manifests
    tools, problems = manifests(ws)
    warnings.extend(problems)
    for tool in tools:
        entries.append({'kind': 'tool', 'id': tool['id'], 'title': tool['id'], 'summary': clean(tool['description'])[:500],
                        'repos': [tool['repo']] if tool.get('repo') else [], 'path': tool['_path'],
                        'status': 'superseded' if tool.get('replaced_by') else 'current', 'updated': '',
                        'terms': sorted(terms(tool['id'] + ' ' + tool['description'] + ' ' + ' '.join(tool['keywords']))),
                        'secrets': list(tool['secrets']), 'effects': tool['effects']})
    return {'schema_version': 1, 'entries': entries, 'warnings': warnings}


def context(ws, query='', repo=None, limit=5, budget=12000):
    if not 1 <= limit <= 20 or budget < 2000:
        raise Error('Context limit must be 1..20 and budget at least 2000 characters.')
    bundle = inventory(ws)
    if query.strip().lower().strip('.!?') in {'hi', 'hello', 'hey'}:
        return {'message': 'Workspace ready. State a task to retrieve context; no historical operations resumed.'}
    selected_repo = ws.resolve_repo(repo) if repo else None
    if repo and not selected_repo:
        bundle['warnings'].append('Repository not registered; searching all work by query.')
    q = terms(query)
    entries = bundle['entries']
    superseded = {x for e in entries if e.get('status') == 'current' for x in e.get('supersedes', [])}
    historical = bool(q & {'history', 'historical', 'old', 'previous', 'superseded'})

    def score(e):
        value = len(q & terms(e['title'])) * 8 + len(q & terms(e['summary'])) * 3 + len(q & set(e['terms']))
        if selected_repo in e['repos'] and selected_repo:
            value += 5
        if (e['status'] == 'superseded' or (e['kind'] not in {'repo', 'task', 'tool'} and e['id'] in superseded)) and not historical:
            value *= .1
        return value

    ranked = sorted(entries, key=lambda e: (score(e), e['updated'], e['id']), reverse=True)
    selected, seen = [], set()
    # Independent buckets ensure a relevant tool is not crowded out by task history.
    for kinds in ({'repo'}, {'system'}, {'learning', 'procedure', 'preference'}, {'task'}, {'tool'}):
        for e in [e for e in ranked if e['kind'] in kinds and score(e) > 0][:limit]:
            selected.append(e)
            seen.add((e['kind'], e['id']))
    connected_repos = {r for e in selected if e['kind'] == 'system' for r in e['repos']}
    connected_memories = {link for e in selected if e['kind'] == 'system' for link in e.get('links', [])}
    for e in entries:
        if (e['kind'] == 'repo' and e['id'] in connected_repos) or (e['kind'] not in {'task', 'tool', 'repo'} and e['id'] in connected_memories):
            if (e['kind'], e['id']) not in seen:
                selected.append(e)
                seen.add((e['kind'], e['id']))
    output = {'query': query, 'repo': selected_repo, 'results': [], 'omitted': 0, 'warnings': bundle['warnings'],
              'note': 'Read cited narratives. Saved facts do not establish current deployment or authorization.',
              'preferences': '.bai/preferences.md'}
    for e in selected:
        item = {k: v for k, v in e.items() if k != 'terms'}
        if e['kind'] not in {'repo', 'task', 'tool'} and e['id'] in superseded:
            item['status'] = 'superseded'
        output['results'].append(item)
        if len(json.dumps(output, ensure_ascii=False)) > budget:
            output['results'].pop()
            output['omitted'] += 1
    return output


def checkpoint(ws, kind, name, expected):
    with ws.lock():
        record = ws.record(kind, name)
        if record['revision'] != expected:
            raise Error('Revision changed; review the latest record before checkpointing.')
        if kind == 'tasks':
            if not record['scope'].strip() or not record['reuse_decision'].strip():
                raise Error('Record scope and reuse_decision before checkpointing (use not applicable when appropriate).')
            from .tooling import manifests
            tools, _ = manifests(ws)
            if set(record['tools']) - {t['id'] for t in tools}:
                raise Error('Task references an unregistered tool.')
            for learning in record['learnings']:
                ws.record('knowledge', learning)
        first = review_hashes(ws, kind, record)
        bundle = inventory(ws)
        if first != review_hashes(ws, kind, record):
            raise Error('Evidence changed during checkpoint; review again.')
        record['reviewed'] = first
        record['revision'] += 1
        record['updated'] = now()
        write_json(ws.record_path(kind, name), record)
        # Cache is derived and disposable; context always reads current records.
        write_json(ws.path('cache/index.json'), inventory(ws))
    result = {'id': name, 'revision': record['revision'], 'checkpointed': True, 'warnings': bundle['warnings']}
    if kind == 'tasks':
        result['memory_review'] = review(ws)
    return result


def doctor(ws):
    cfg = ws.require()
    errors, warnings = [], []
    for repo in cfg['repositories']:
        try:
            ws.repo_path(repo)
        except Error:
            warnings.append(f'Repository unavailable; rebind before use: {repo}')
    for kind in ('tasks', 'knowledge'):
        records, invalid = ws.records(kind)
        errors.extend(invalid)
        for record in records:
            try:
                body(ws, kind, record['id'])
                if kind == 'tasks' and review_hashes(ws, kind, record) != record.get('reviewed'):
                    warnings.append(f'Task needs handoff review and checkpoint: {record["id"]}')
            except (Error, OSError):
                errors.append(f'Missing narrative: {kind}/{record["id"]}')
    from .tooling import manifests, inspect_tool
    tools, invalid = manifests(ws)
    errors.extend(invalid)
    for tool in tools:
        warnings.extend(inspect_tool(ws, tool)['warnings'])
    bundle = inventory(ws)
    cache = ws.path('cache/index.json')
    if cache.exists():
        try:
            if read_json(cache) != bundle:
                warnings.append('Derived cache is stale; context remains current. Next checkpoint will refresh it.')
        except Error:
            warnings.append('Derived cache is unreadable; next checkpoint will replace it.')
    findings = review(ws)
    if findings['findings']:
        warnings.append(f'{len(findings["findings"])} memory review finding(s); run memory review.')
    if not shutil.which('age'):
        warnings.append('age is not installed; encrypted backup/restore unavailable until installed.')
    for path in ws.data.rglob('*'):
        if path.is_symlink():
            errors.append('Personal workspace contains a symlink; replace it with an explicit external repository binding.')
            break
    ignore = ws.root / '.gitignore'
    if ignore.exists() and '/.bai/' not in ignore.read_text(encoding='utf-8').splitlines():
        warnings.append('The clone .gitignore lacks /.bai/; restore this ignore rule before staging files.')
    return {'ok': not errors, 'errors': errors, 'warnings': warnings, 'tasks': len(ws.records('tasks')[0]), 'tools': len(tools)}
