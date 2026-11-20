"""Bounded retrieval and explicit review of file-backed evidence."""
from __future__ import annotations

import hashlib
import json
import math
import collections
import re
import shutil

from .core import Error, PRIVATE_NAMES, UNFINISHED_STATES, clean, contained, digest, encoded, now, read_json, write_json


WORD_FORMS = {
    'tests': 'test', 'tested': 'test', 'testing': 'test',
    'verification': 'verify', 'verifies': 'verify', 'verified': 'verify', 'verifying': 'verify',
    'checks': 'check', 'checked': 'check', 'checking': 'check',
    'scripts': 'script', 'files': 'file',
}
RESUME_QUERIES = {
    'continue', 'continue work', 'continue yesterday s work', 'resume', 'resume work',
    'continue where we left off', 'pick up where we left off', 'ongoing', 'ongoing work',
    'current work', 'active tasks', 'what were we working on', 'use the scope i gave you last time',
}
BRIEF_STOP = set('a an are as at be can could did do does get give had has have here i in is it last left me my need now of on off or our s some still take tell there to up us was we were where which who will work would you your yesterday again'.split())
CONTINUATION = {'continue', 'resume', 'revisit', 'ongoing', 'unfinished', 'active', 'open', 'remaining'}
WAIT_ORDER = {'user': 0, 'external': 1, 'unknown': 2}
INPUT_ACTIONS = r'provides?|sends?|uploads?|shares?|supplies|supply|opens?|selects?|confirms?|grants?|reviews?|connects?|attach(?:es)?|enters?|runs?|chooses?'
USER_ACTION = re.compile(
    r'\b(?:waiting (?:on|for)|blocked (?:on|by)|depends on|awaits?|requires?) (?:the )?(?:user|you)\b'
    rf'|\b(?:user|you) (?:must |needs? to |should |has to |have to |to )?(?:{INPUT_ACTIONS})\b'
    rf'|\b(?:please|could you|can you) (?:{INPUT_ACTIONS})\b'
    r'|\buser (?:action|input|confirmation|approval) (?:is )?(?:needed|required|pending)\b'
    r'|\b(?:need|await|pending|require|waiting|obtain)\w*\b.{0,100}\bfrom (?:the )?(?:user|you)\b',
    re.IGNORECASE,
)
EXTERNAL_WAIT = re.compile(
    r'\b(?:waiting|pending|blocked|await\w*|poll|monitor)\b.{0,100}\b(?:external|third.party|provider|'
    r'queue|job|background|workflow|service|worker|pipeline)\b'
    r'|\b(?:upstream|external|remote|background|provider|pipeline|publisher|queue)\b.{0,65}'
    r'\b(?:must|has to|needs? to|still has to|will) (?:finish|complete|publish|run|deliver|respond)\b',
    re.IGNORECASE,
)


def terms(text):
    stop = {'the', 'and', 'for', 'with', 'from', 'this', 'that', 'what', 'how', 'please', 'repo',
            'current', 'state', 'ongoing', 'continue', 'implementation', 'review', 'show'}
    words = set(re.findall(r'[a-z0-9]+', clean(text).lower())) - stop
    return {WORD_FORMS.get(word, word) for word in words}


def excerpt(text, limit):
    return text if len(text) <= limit else text[:limit - 3] + '...'


def words(value):
    return re.findall(r'[^\W_]+', clean(value).casefold())


def brief_intent(query):
    query_words = words(query)
    tokens = set(query_words)
    phrase = ' '.join(query_words).removeprefix('please ').removesuffix(' please')
    inventory_request = bool(tokens & {'what', 'which', 'anything', 'show', 'list', 'status', 'where'})
    if (tokens & {'waiting', 'blocked', 'blocker', 'blockers', 'pending', 'awaiting'}
            and (inventory_request or len(tokens - BRIEF_STOP) <= 2)) or (
                inventory_request and tokens & {'me', 'i', 'my'} and tokens & {'need', 'needs', 'needed', 'required'}):
        return 'waiting'
    if (not tokens or phrase in RESUME_QUERIES or (tokens & CONTINUATION and (
            tokens & {'continue', 'resume', 'revisit', 'ongoing', 'unfinished'} or inventory_request or 'tasks' in tokens))
            or {'pick', 'up'} <= tokens
            or (inventory_request and tokens & {'working', 'doing', 'left', 'handoff'})):
        return 'resume'
    return 'search'


def brief_body(ws, kind, name):
    try:
        with ws.path(f'{kind}/{name}.md').open(encoding='utf-8') as stream:
            return clean(stream.read(16000))
    except (Error, OSError, UnicodeError):
        return ''


def waiting_details(ws, task):
    if 'waiting_on' in task:
        return {'waiting_on': task['waiting_on']}
    if task['status'] != 'waiting':
        return {'waiting_on': 'unknown'}
    sources = (('next_step', clean(task['next_step'])), ('summary', clean(task['summary'])),
               ('verification', clean(task['verification'])))
    def infer(source, value):
        no_user_action = re.search(r'\b(?:(?:no|without) (?:further )?(?:user|human) (?:action|input|approval)'
                                   r'|(?:user|you) (?:has|have) no (?:action|input))\b', value, re.IGNORECASE)
        for party, pattern in (('user', USER_ACTION), ('external', EXTERNAL_WAIT)):
            if party == 'user' and no_user_action:
                continue
            match = pattern.search(value)
            if match:
                result = {'waiting_on': party, 'waiting_on_inferred': source}
                if source != 'next_step' or match.start() > 60:
                    # Center the excerpt on the phrase that supports the inference.
                    start = max(0, match.start() - 25)
                    result['waiting_evidence'] = ('...' if start else '') + excerpt(value[start:], 170)
                return result
        return None

    for source, value in sources:
        inferred = infer(source, value)
        if inferred:
            return inferred
    for line in brief_body(ws, 'tasks', task['id']).splitlines():
        if re.search(r'\b(?:once|until|pending|waiting|blocked|please|needs?|must|requires?|await\w*)\b', line, re.IGNORECASE):
            inferred = infer('narrative', line)
            if inferred:
                return inferred
    return {'waiting_on': 'unknown'}


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
    # Evidence documents may legitimately discuss credentials. Reject secret file
    # formats, not Markdown task folders named credential-validation.
    credential_file = PRIVATE_NAMES.search(name.split('/')[-1]) and not name.endswith('.md')
    if credential_file or any(part in {'.git', 'private'} or part.startswith('.env') for part in name.lower().split('/')):
        raise Error('Credential and Git-internal paths cannot be memory sources.')
    if repo == 'evidence':
        return ws.path('artifacts/' + name)
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


def evidence_issue(ws, record):
    try:
        if review_hashes(ws, 'knowledge', record) != record.get('reviewed'):
            return 'Narrative, metadata or cited source changed since review (or has never been reviewed).'
    except (Error, OSError):
        return 'A narrative or cited source is unavailable; inspect it before relying on this memory.'
    return None


def review(ws, repo=None, include_historical=False):
    ws.require()
    records, warnings = ws.records('knowledge')
    ids = {r['id'] for r in records}
    findings = []
    historical_pending = 0
    for record in records:
        if repo and repo not in record['repos']:
            continue
        if record.get('historical') and not include_historical:
            historical_pending += 1
            continue
        reasons = []
        if record['status'] == 'uncertain':
            reasons.append('Understanding is marked uncertain.')
        issue = evidence_issue(ws, record)
        if issue:
            reasons.append(issue)
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
    current = [r for r in records if r['status'] == 'current' and (include_historical or not r.get('historical')) and (not repo or repo in r['repos'])]
    for i, first in enumerate(current):
        for second in current[i + 1:]:
            explicitly_related = second['id'] in first['links'] or first['id'] in second['links']
            if first['kind'] == second['kind'] and not explicitly_related and set(first['aliases']) & set(second['aliases']) and not ({first['id']} & set(second['supersedes']) or {second['id']} & set(first['supersedes'])):
                findings.append({'id': first['id'], 'reasons': [f'Shared aliases with {second["id"]}; check for duplicate or conflicting claims.']})
    return {'findings': findings, 'warnings': warnings, 'historical_pending': historical_pending,
            'note': 'Structural review only. Historical imports require evidence review on use; use --include-historical for the full audit.'}


def inventory(ws, *, read_bodies=True, task_records=None):
    cfg = ws.require()
    entries, warnings = [], []
    for kind in ('tasks', 'knowledge'):
        records, problems = task_records if kind == 'tasks' and task_records is not None else ws.records(kind)
        warnings.extend(problems)
        for r in records:
            narrative = ''
            if read_bodies:
                try:
                    narrative = body(ws, kind, r['id'])
                except (Error, OSError, UnicodeError):
                    warnings.append(f'Missing narrative for {kind}/{r["id"]}.')
            scope, next_step = clean(r.get('scope', '')), clean(r.get('next_step', ''))
            routing_text = ' '.join([r['title'], *r['aliases'], scope, next_step])
            compact_fields = {}
            if not read_bodies:
                compact_fields = {'aliases': r['aliases'], 'record': r}
                routing_text += ' ' + ' '.join([r['id'], r.get('done_when', ''), r.get('verification', '')])
            entries.append({'kind': 'task' if kind == 'tasks' else r['kind'], 'id': r['id'], 'title': clean(r['title']),
                            'summary': clean(r['summary'])[:500], 'repos': r['repos'], 'status': r['status'], 'updated': r['updated'],
                            'path': f'.bai/{kind}/{r["id"]}.json', 'narrative': f'.bai/{kind}/{r["id"]}.md',
                            'links': r.get('links', []), 'supersedes': r.get('supersedes', []),
                            'provenance': r.get('provenance'), 'reviewed': bool(r.get('reviewed')),
                            'historical': r.get('historical', False), 'source_date': r.get('origin', {}).get('source_date'),
                            'scope': excerpt(scope, 400), 'next_step': excerpt(next_step, 400), 'sources': r.get('sources', [])[:3],
                            'related_tasks': r.get('related_tasks', []),
                            'tools': r.get('tools', []), 'learnings': r.get('learnings', []),
                            'routing_terms': sorted(terms(routing_text)),
                            'terms': sorted(terms(' '.join([routing_text, r['summary'], narrative]))), **compact_fields})
    for repo, data in cfg['repositories'].items():
        text = ''
        if read_bodies:
            try:
                text = ws.path(f'repos/{repo}.md').read_text(encoding='utf-8')
            except (Error, OSError, UnicodeError):
                warnings.append(f'Missing repository brief: {repo}')
        entries.append({'kind': 'repo', 'id': repo, 'title': repo, 'summary': clean(data['summary'])[:500], 'repos': [repo],
                        'path': f'.bai/repos/{repo}.md', 'status': 'current', 'updated': data['registered'],
                        'aliases': data['aliases'],
                        'terms': sorted(terms(repo + ' ' + data['summary'] + ' ' + ' '.join(data['aliases']) + ' ' + text))})
    from .tooling import manifests, search_terms
    tools, problems = manifests(ws)
    warnings.extend(problems)
    for tool in tools:
        entries.append({'kind': 'tool', 'id': tool['id'], 'title': tool['id'], 'summary': clean(tool['description'])[:500],
                        'repos': [tool['repo']] if tool.get('repo') else [], 'path': tool['_path'],
                        'status': 'superseded' if tool.get('replaced_by') else 'current', 'updated': '',
                        'aliases': tool.get('aliases', []), 'terms': sorted(search_terms(tool)),
                        'secrets': list(tool['secrets']), 'effects': tool['effects']})
    return {'schema_version': 1, 'entries': entries, 'warnings': warnings}


def resume_handoffs(ranked, query_terms, repo, waiting_only=False):
    states = {'waiting'} if waiting_only else UNFINISHED_STATES
    tasks = {e['id']: e for e in ranked if e['kind'] == 'task' and not e.get('historical')
             and e['status'] in states and (not repo or repo in e['repos'])}
    if waiting_only:
        return sorted(tasks.values(), key=lambda e: (e['updated'], e['id']), reverse=True)
    coverage = {name: len(query_terms.intersection(task['terms'])) for name, task in tasks.items()}
    best_coverage = max(coverage.values(), default=0)
    neighbors = {name: set() for name in tasks}
    for name, task in tasks.items():
        for link in task['related_tasks']:
            if link in tasks:
                neighbors[name].add(link)
                neighbors[link].add(name)

    # A match to an older handoff can lead to its newer linked task. A group is
    # a continuation candidate, not evidence that its other tasks are closed.
    seen, handoffs = set(), []
    for name in tasks:
        if name in seen or (query_terms and (not best_coverage or coverage[name] < best_coverage)):
            continue
        group, pending = [], [name]
        seen.add(name)
        while pending:
            member = pending.pop()
            group.append(tasks[member])
            unseen = neighbors[member] - seen
            seen.update(unseen)
            pending.extend(unseen)
        handoffs.append(max(group, key=lambda e: (e['updated'], e['id'])))
    if not query_terms:
        handoffs.sort(key=lambda e: (e['updated'], e['id']), reverse=True)
    return handoffs


def name_matches(phrase, entry):
    names = [' '.join(words(value)) for value in (entry['id'], entry['title'], *entry.get('aliases', []))]
    return {name for name in names if name and f' {name} ' in phrase}


def named_entries(phrase, entries):
    hits = [(entry, name) for entry in entries for name in name_matches(phrase, entry)]
    return [entry for entry in entries if any(
        match is entry and not any(f' {name} ' in f' {other} ' and name != other for _, other in hits)
        for match, name in hits)]


def brief_rank(entry):
    return entry['search_score'], entry['updated'], entry['id']


def relevant(entries):
    ranked = sorted(entries, key=brief_rank, reverse=True)
    if not ranked:
        return []
    threshold = max(1, ranked[0]['search_score'] * .55)
    return [e for e in ranked if e['search_score'] >= threshold]


def brief_scores(ws, entries, phrase, query_words, repo, *, search_narratives):
    for entry in entries:
        aliases = name_matches(phrase, entry)
        entry['search_score'] = (max((30 + len(name.split()) * 10 for name in aliases), default=0)
                                 + 4 * len(query_words & set(entry['terms']))
                                 + 2 * len(query_words & set(entry.get('routing_terms', []))))
    if search_narratives and query_words and not any(entry['search_score'] for entry in entries):
        # A legacy record can mention the subject only in its narrative. Prefer
        # recent, scoped candidates for that fallback and leave the index alone.
        candidates = sorted((e for e in entries if e.get('narrative') and not e['historical']
                             and (not repo or repo in e['repos'])), key=lambda e: (e['updated'], e['id']), reverse=True)
        for entry in candidates[:24]:
            kind = 'tasks' if entry['kind'] == 'task' else 'knowledge'
            if query_words & terms(brief_body(ws, kind, entry['id'])):
                entry['search_score'] = 1
        return len(candidates) > 24
    return False


def brief_task_row(ws, entry):
    record = entry['record']
    row = {k: entry[k] for k in ('kind', 'id', 'title', 'status', 'narrative')}
    row.update(revision=record['revision'], updated=entry['updated'], summary=clean(record['summary']),
               scope=clean(record['scope']), done_when=clean(record.get('done_when', '')),
               verification=clean(record['verification']), next_step=clean(record['next_step']),
               topic=record.get('topic'), repos=entry['repos'], related_tasks=entry['related_tasks'])
    if record['status'] in UNFINISHED_STATES and not entry['historical']:
        row.update(waiting_details(ws, record))
    else:
        row['historical'] = True
    return row


def brief_memory_row(ws, entry, requirement, superseded):
    record = entry['record']
    row = {k: entry[k] for k in ('kind', 'id', 'title', 'status', 'narrative', 'provenance')}
    row.update(summary=clean(record['summary']), sources=record['sources'])
    if requirement:
        row['requirement'] = True
    if entry['id'] in superseded:
        row['status'] = 'superseded'
    if entry['historical'] or row['status'] == 'superseded':
        row['historical'] = True
    if row['status'] == 'current' and not row.get('historical'):
        row['review_needed'] = (evidence_issue(ws, record) if record['sources']
                                else 'No source is recorded. Review before relying on this record.')
    elif requirement:
        row['review_needed'] = 'Review the requirement and identify a current source.'
    return row


def brief_topic_row(topic):
    return {'kind': 'topic', 'id': topic['id'], 'title': clean(topic['title']), 'purpose': clean(topic['purpose']),
            'reference_repos': topic['reference_repos'], 'implementation_repos': topic['implementation_repos']}


def context_brief(ws, query, repo, topic, limit, budget):
    task_records = ws.records('tasks')
    topics, topic_warnings = ws.records('topics')
    bundle = inventory(ws, read_bodies=False, task_records=task_records)
    warnings = [*bundle['warnings'], *topic_warnings]
    entries = bundle['entries']
    repos = [entry for entry in entries if entry['kind'] == 'repo']
    tasks = [entry for entry in entries if entry['kind'] == 'task']
    tools = [entry for entry in entries if entry['kind'] == 'tool' and entry['status'] == 'current']
    memories = [entry for entry in entries if entry['kind'] not in {'task', 'tool', 'repo'}]
    memory_by_id = {entry['id']: entry for entry in memories}
    topics_by_id = {item['id']: item for item in topics}
    selected_repo = ws.resolve_repo(repo) if repo else None
    if repo and not selected_repo:
        warnings.append('Repository not registered. Searching by query.')
    phrase = ' ' + ' '.join(words(query)) + ' '
    repo_hits = named_entries(phrase, repos)
    if selected_repo is None and len(repo_hits) == 1:
        selected_repo = repo_hits[0]['id']
    intent = brief_intent(query)
    q = terms(query) - BRIEF_STOP
    if intent != 'search':
        q -= CONTINUATION | {'anything', 'awaiting', 'blocked', 'blocker', 'blockers', 'doing', 'handoff',
                             'needs', 'pending', 'pick', 'required', 'status', 'task', 'tasks', 'waiting', 'working'}
    if intent == 'waiting':
        q -= {'action', 'actions', 'before', 'complete', 'completion', 'else', 'finish', 'needed', 'side'}
        q = {word for word in q if not re.fullmatch(INPUT_ACTIONS, word)}
    if topic is not None:
        selected_id = ws.resolve_topic(topic)
        if selected_id is None:
            raise Error('Topic is not registered. Use topic list to see available topics.')
        topic_hits = [topics_by_id[selected_id]]
    else:
        topic_hits = named_entries(phrase, topics)
    search_limited = brief_scores(ws, [*tasks, *memories, *tools], phrase, q, selected_repo,
                                  search_narratives=intent != 'waiting' and not topic_hits)
    if search_limited:
        warnings.append('Narrative fallback searched the 24 most recent eligible records.')
    if not topic_hits and q:
        hits = relevant([e for e in tasks if e['record'].get('topic') in topics_by_id])
        minimum_score = 40 if intent == 'waiting' else 8
        topic_ids = dict.fromkeys(e['record']['topic'] for e in hits if e['search_score'] >= minimum_score)
        topic_hits = [topics_by_id[name] for name in topic_ids]
    selected = topic_hits[0] if len(topic_hits) == 1 else None
    role_repos = set(selected['implementation_repos'] + selected['reference_repos']) if selected else set()
    focus_repos = role_repos if selected else {selected_repo} - {None}
    if selected_repo:
        q -= terms(' '.join([selected_repo, *(alias for e in repo_hits for alias in e['aliases'])]))

    def in_scope(entry):
        return not focus_repos or bool(focus_repos.intersection(entry['repos']))

    def in_topic(entry):
        bound = entry['record'].get('topic')
        if bound:
            return bound == selected['id']
        values = [entry['title'], clean(entry['record']['summary']), *entry['aliases']]
        return bool(name_matches(' ' + ' '.join(words(' '.join(values))) + ' ', selected))

    if selected:
        eligible = [e for e in tasks if in_topic(e)]
    elif len(topic_hits) > 1:
        choice_ids = {item['id'] for item in topic_hits}
        eligible = [e for e in tasks if e['record'].get('topic') in choice_ids]
    else:
        eligible = [e for e in tasks if in_scope(e)]
        if q and intent != 'waiting' and not relevant(eligible):
            eligible = [e for e in tasks if e['search_score'] >= 8]
    opened = [e for e in eligible if e['status'] in UNFINISHED_STATES and not e['historical']]
    if intent == 'waiting':
        opened = [e for e in opened if e['status'] == 'waiting']
    matches = relevant(opened) if q and not topic_hits else []
    if intent == 'waiting':
        matches = [e for e in matches if e['search_score'] >= 40]
    if q and not topic_hits and (intent != 'waiting' or matches):
        linked = {link for e in matches for link in e['related_tasks']}
        opened = [*matches, *(e for e in opened if e not in matches and (e['id'] in linked
                  or any(match['id'] in e['related_tasks'] for match in matches)))]
    else:
        opened.sort(key=brief_rank, reverse=True)
    if intent == 'waiting' and not topic_hits and opened:
        waiting_topics = {e['record'].get('topic') for e in opened}
        if len(waiting_topics) == 1:
            selected = topics_by_id.get(next(iter(waiting_topics)))

    task_rows = [brief_task_row(ws, entry) for entry in opened]
    if intent == 'waiting':
        task_rows.sort(key=lambda row: WAIT_ORDER[row['waiting_on']])

    closed = []
    topic_terms = terms(' '.join(name_matches(phrase, selected))) if selected else set()
    targeted = bool(q - topic_terms)
    if targeted and intent != 'waiting':
        closed = relevant([e for e in eligible if e['status'] not in UNFINISHED_STATES or e['historical']])
        best_open = max((e['search_score'] for e in opened), default=0)
        if task_rows:
            closed = [e for e in closed if e['search_score'] >= 8 and e['search_score'] > best_open * 1.5]
    candidates = (relevant(opened) if targeted else []) or opened
    choice_ids = set()
    if intent != 'waiting' and not closed and len(topic_hits) <= 1 and len(candidates) > 1:
        choice_ids = {entry['id'] for entry in candidates}
        task_rows.sort(key=lambda row: row['id'] not in choice_ids)
    # A recurrence can match a closed outcome while another outcome in the same
    # topic is still pending. Preserve that distinction in the lead row.
    rows = [*(brief_task_row(ws, entry) for entry in closed), *task_rows]
    requirements = (selected['requirements'] if selected else [])
    superseded = {name for e in memories if e['status'] == 'current' and not e['historical'] for name in e['supersedes']}
    requirement_rows = []
    requirement_warnings = []
    for name in requirements:
        if name in memory_by_id:
            row = brief_memory_row(ws, memory_by_id[name], True, superseded)
            requirement_rows.append(row)
            if row.get('review_needed'):
                requirement_warnings.append(f'Review topic requirement {name} before relying on it.')
        else:
            requirement_warnings.append(f'Missing topic requirement: {name}.')
    warnings = [*requirement_warnings, *warnings]
    if selected:
        # Show available choices before spending rows on supporting context.
        position = (len(rows) if intent == 'waiting' else len(choice_ids) or min(1, len(rows)))
        rows[position:position] = [brief_topic_row(selected), *requirement_rows]
    elif topic_hits:
        rows[0:0] = [brief_topic_row(item) for item in topic_hits]

    if (q or focus_repos) and intent != 'waiting' and len(topic_hits) <= 1:
        declared_memories = {name for task in opened for name in task['learnings']}
        declared_tools = {name for task in opened for name in task['tools']}
        current_memories = [e for e in memories if e['id'] not in requirements and e['id'] not in superseded
                            and e['status'] == 'current' and not e['historical']]
        related_memories = [e for e in current_memories if e['id'] in declared_memories]
        matching_memories = relevant([e for e in current_memories if in_scope(e) and e not in related_memories])
        if focus_repos and not q:
            matching_memories = [e for e in current_memories if in_scope(e) and e['kind'] in {'system', 'preference'}
                                 and e not in related_memories]
        referenced_repos = focus_repos | {r for row in task_rows for r in row['repos']}
        repo_rows = [{k: e[k] for k in ('kind', 'id', 'summary', 'path')} for e in repos if e['id'] in referenced_repos]
        if selected_repo and not selected and not q and not choice_ids:
            rows[0:0] = repo_rows
            repo_rows = []
        rows.extend(brief_memory_row(ws, e, False, superseded) for e in [*related_memories, *matching_memories])
        tool_hits = relevant([e for e in tools if in_scope(e)])
        rows.extend({k: e[k] for k in ('kind', 'id', 'summary', 'effects', 'path')} for e in tools
                    if e['id'] in declared_tools or e in tool_hits)
        rows.extend(repo_rows)

    selected_id = selected['id'] if selected else None
    output = {'query': excerpt(clean(query), 150), 'repo': selected_repo, 'topic': selected_id,
              'open_set': ws.open_set(task_records[0], selected_id), 'results': [], 'omitted': len(rows),
              'warnings': [], 'warning_count': len(warnings), 'truncated': 0,
              'preferences': '.bai/preferences.md',
              'note': ('Use this packet for recorded status, verification and ownership follow-ups. Role IDs identify '
                       'the checkouts. Reopen details as needed. Verify current state and authority before making changes.'),
              'resume': {'candidate_count': len(opened),
                         'needs_choice': bool(choice_ids) or len(topic_hits) > 1}}
    if requirements:
        output['requirements'] = {'count': len(requirements)}
        if requirement_warnings:
            output['requirements']['review_needed'] = len(requirement_warnings)
    if len(topic_hits) > 1:
        output['topic_candidate_count'] = len(topic_hits)
    if intent == 'waiting':
        counts = collections.Counter(row['waiting_on'] for row in task_rows)
        output['waiting_on_counts'] = {party: counts[party] for party in WAIT_ORDER}
    return fit_brief(output, rows, warnings, limit, budget, choice_ids=choice_ids)


def fit_brief(output, rows, warnings, limit, budget, *, choice_ids):
    def size():
        return len(json.dumps(output, ensure_ascii=False, indent=2)) + 1

    def compact_row(row, width):
        row = {k: v for k, v in row.items() if v not in ('', [], None)}
        if not width and row['kind'] == 'task' and row['id'] in choice_ids:
            choice = {k: row[k] for k in ('kind', 'id', 'title', 'status')}
            choice['title'] = excerpt(choice['title'], 75)
            distinction = next((k for k in ('done_when', 'scope', 'next_step', 'summary') if k in row), None)
            if distinction:
                choice[distinction] = excerpt(row[distinction], 75)
            return choice, choice != row
        width = width or 40
        prose = {'title': max(width, 75), 'scope': width, 'summary': width, 'purpose': width,
                 'done_when': max(width, 75), 'verification': max(width, 75),
                 'next_step': max(width, 120), 'waiting_evidence': max(width, 120)}
        compact = {key: excerpt(value, prose[key]) if key in prose else value for key, value in row.items()}
        if width <= 90:
            for key in ('related_tasks', 'sources', 'repos', 'reference_repos', 'implementation_repos'):
                if len(compact.get(key, [])) > 2:
                    compact[key] = compact[key][:2]
            compact.pop('updated', None)
        if width <= 40:
            if row['kind'] == 'task':
                compact.pop('summary', None)
            compact.pop('related_tasks', None)
            compact.pop('repos', None)
        return compact, compact != row

    # Reserve a warning when present, even under a one-row limit.
    output['warnings'] = [excerpt(warnings[0], 180)] if warnings else []
    # Keep the largest ordered set of rows the budget can represent. Compact
    # every row before dropping a lower-ranked row entirely.
    widths = (360, 180, 90, 40, 0) if choice_ids else (360, 180, 90, 40)
    for count in range(min(limit, len(rows)), -1, -1):
        for width in widths:
            compact = [compact_row(row, width) for row in rows[:count]]
            output['results'] = [row for row, _ in compact]
            output['omitted'] = len(rows) - count
            output['truncated'] = sum(short for _, short in compact)
            requirements = output.get('requirements')
            if requirements:
                complete = sum(bool(row.get('requirement')) and not short for row, short in compact)
                if requirements.get('review_needed') or complete < requirements['count']:
                    requirements['inspect'] = f'topic show {output["topic"]}'
                else:
                    requirements.pop('inspect', None)
            if size() <= budget:
                for warning in warnings[1:]:
                    output['warnings'].append(excerpt(warning, 180))
                    if size() > budget:
                        output['warnings'].pop()
                        break
                return output
    return output


def context(ws, query='', repo=None, limit=5, budget=None, *, brief=False, topic=None):
    compact = brief or topic is not None
    if budget is None:
        budget = 4000 if compact else 12000
    if not 1 <= limit <= 20 or budget < 2000:
        raise Error('Context limit must be 1..20 and budget at least 2000 characters.')
    if query.strip().lower().strip('.!?') in {'hi', 'hello', 'hey'}:
        return {'message': 'Workspace ready. State a task to retrieve context; no historical operations resumed.'}
    if compact:
        return context_brief(ws, query, repo, topic, limit, budget)
    bundle = inventory(ws)
    selected_repo = ws.resolve_repo(repo) if repo else None
    if repo and not selected_repo:
        bundle['warnings'].append('Repository not registered; searching all work by query.')
    phrase = ' '.join(re.findall(r'[a-z0-9]+', query.lower()))
    phrase = phrase.removeprefix('please ').removesuffix(' please')
    waiting_only = brief_intent(query) == 'waiting'
    resume = phrase in RESUME_QUERIES or waiting_only
    named_resume = re.match(r'^(?:continue|resume|pick up) (.+)$', phrase)
    resuming = resume or bool(named_resume)
    q = set() if resume else terms(named_resume[1] if named_resume else query)
    entries = bundle['entries']
    frequency = collections.Counter(word for e in entries for word in set(e['terms']))
    def weight(words):
        return sum(1 + math.log((len(entries) + 1) / (frequency[word] + 1)) for word in words)
    superseded = {x for e in entries if e.get('status') == 'current' for x in e.get('supersedes', [])}
    historical = bool(q & {'history', 'historical', 'old', 'previous', 'superseded'})

    def score(e):
        value = weight(q & terms(e['title'])) * 8 + weight(q & terms(e['summary'])) * 3 + weight(q & set(e['terms']))
        if selected_repo in e['repos'] and selected_repo:
            value += 5
        if value and e['kind'] == 'task' and e['status'] in UNFINISHED_STATES and not e.get('historical'):
            value += 12
        if e.get('historical') and not historical:
            value *= .6
        if (e['status'] == 'superseded' or (e['kind'] not in {'repo', 'task', 'tool'} and e['id'] in superseded)) and not historical:
            value *= .1
        return value

    ranked = sorted(entries, key=lambda e: (score(e), e['updated'], e['id']), reverse=True)
    selected, seen = [], set()
    if resuming:
        handoffs = resume_handoffs(ranked, q, selected_repo, waiting_only)
        if waiting_only:
            for entry in handoffs:
                entry.update(waiting_details(ws, ws.record('tasks', entry['id'])))
            handoffs.sort(key=lambda entry: WAIT_ORDER[entry['waiting_on']])
        current_tasks = handoffs[:limit]
    else:
        current_tasks = [e for e in ranked if e['kind'] == 'task' and not e.get('historical') and score(e) > 0
                         and (q.intersection(e['routing_terms']) or (not q and selected_repo in e['repos']))]
        if current_tasks:
            threshold = score(current_tasks[0]) * .35
            current_tasks = [e for e in current_tasks if score(e) >= threshold][:min(limit, 3)]
    other_tasks = []
    if not (resume or (named_resume and current_tasks)):
        other_tasks = [e for e in ranked if e['kind'] == 'task' and e not in current_tasks and score(e) > 0]
    declared_tools = list(dict.fromkeys(name for e in current_tasks for name in e['tools']))
    declared_memories = list(dict.fromkeys(name for e in current_tasks for name in e['learnings']))
    buckets = [current_tasks if resuming else current_tasks[:1]]
    # Explicit handoff links outrank loosely related keyword matches in every
    # section, so a tight budget cannot lose the current procedure/source gaps.
    direct_memories = [e for name in declared_memories for e in ranked if e['id'] == name and e['kind'] not in {'task', 'tool', 'repo'}]
    if not current_tasks:
        lead = next((e for e in ranked if e['kind'] == 'system' and score(e) > 0), None)
        if lead:
            neighbors = [e for e in ranked if e['kind'] not in {'task', 'tool', 'repo'} and e['id'] in lead.get('links', [])]
            direct_memories = [lead, *neighbors[:3]]
    buckets.append(direct_memories[:min(limit + 1, 6)])
    direct_tools = [e for name in declared_tools for e in ranked if e['kind'] == 'tool' and e['id'] == name]
    buckets.append(direct_tools[:min(limit, 3)])
    if not resuming:
        buckets.append(current_tasks[1:])
    lead_repos = set(current_tasks[0]['repos']) if current_tasks and resuming else set()
    for kinds in ({'system'}, {'tool'}, {'learning', 'procedure', 'preference'}, {'repo'}):
        matches = [e for e in ranked if e['kind'] in kinds and e not in direct_memories and e not in direct_tools
                   and (score(e) > 0 or (e['kind'] == 'system' and lead_repos.intersection(e['repos'])))]
        buckets.append(matches[:min(limit, 3)])
    buckets.append(other_tasks[:limit])
    for bucket in buckets:
        for e in bucket:
            selected.append(e)
            seen.add((e['kind'], e['id']))
    connected_repos = {r for e in selected if e['kind'] == 'system' for r in e['repos']}
    connected_memories = {link for e in selected if e['kind'] == 'system' for link in e.get('links', [])}
    for e in entries:
        if (e['kind'] == 'repo' and e['id'] in connected_repos) or (e['kind'] not in {'task', 'tool', 'repo'} and e['id'] in connected_memories):
            if (e['kind'], e['id']) not in seen:
                selected.append(e)
                seen.add((e['kind'], e['id']))
    output = {'query': query[:500], 'repo': selected_repo, 'results': [], 'omitted': len(selected), 'warnings': [], 'warning_count': len(bundle['warnings']),
              'note': 'Read cited narratives. Saved facts do not establish current deployment or authorization.',
              'preferences': '.bai/preferences.md', 'related_repositories': []}
    if resuming:
        output['resume'] = {'candidate_count': len(handoffs), 'needs_choice': len(handoffs) > 1}

    def size():
        return len(json.dumps(output, ensure_ascii=False, indent=2)) + 1

    # JSON escaping can expand the query substantially when it contains control characters.
    if size() > budget:
        output['query'] = clean(query)[:100]
    fields = {'kind', 'id', 'title', 'summary', 'path', 'narrative', 'status', 'updated', 'historical',
              'source_date', 'provenance', 'scope', 'next_step', 'related_tasks', 'secrets', 'effects',
              'waiting_on', 'waiting_on_inferred'}
    displayed_repos = set()
    for e in selected:
        item = {k: v for k, v in e.items() if k in fields and v not in (None, '', [])}
        if 'related_tasks' in item:
            item['related_tasks'] = item['related_tasks'][:5]
        is_memory = e['kind'] not in {'repo', 'task', 'tool'}
        if is_memory and e['id'] in superseded:
            item['status'] = 'superseded'
        output['results'].append(item)
        first = len(output['results']) == 1
        if (first or size() <= budget) and is_memory and item['status'] == 'current':
            try:
                record = ws.record('knowledge', e['id'])
                issue = (evidence_issue(ws, record) if record['updated'] == e['updated']
                         else 'Memory changed during retrieval. Read its latest record.')
            except Error:
                issue = 'Memory is unavailable. Read its latest record before relying on it.'
            if issue:
                item['review_needed'] = issue
        if first and size() > budget:
            # Keep an actionable lead even when one handoff exceeds the budget.
            output['query'] = output['query'][:100]
            for key in ('summary', 'scope', 'next_step', 'title'):
                if key in item:
                    item[key] = excerpt(item[key], 140)
            item.pop('source_date', None)
        if size() > budget:
            output['results'].pop()
            continue
        output['omitted'] -= 1
        if e['kind'] == 'system':
            displayed_repos.update(e['repos'])

    for target, items in (
        ('warnings', bundle['warnings']),
        ('related_repositories', [{'id': r, 'brief': f'.bai/repos/{r}.md'} for r in sorted(displayed_repos)]),
    ):
        for item in items:
            output[target].append(item)
            if size() > budget:
                output[target].pop()
                break
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
            for related in record.get('related_tasks', []):
                ws.record('tasks', related)
        first = review_hashes(ws, kind, record)
        if first != review_hashes(ws, kind, record):
            raise Error('Evidence changed during checkpoint; review again.')
        revised = {**record, 'reviewed': first, 'revision': expected + 1, 'updated': now()}
        ws.commit_revision(kind, record, revised)
        result = {'id': name, 'revision': revised['revision'], 'checkpointed': True,
                  'cache_refreshed': False, 'warnings': []}
        # Cache is derived and disposable; context always reads current records.
        try:
            bundle = inventory(ws)
            result['warnings'].extend(bundle['warnings'])
            write_json(ws.path('cache/index.json'), bundle)
        except Exception:
            result['warnings'].append('Checkpoint saved. The derived cache could not be refreshed.')
        else:
            result['cache_refreshed'] = True
    if kind == 'tasks':
        try:
            result['memory_review'] = review(ws)
        except Exception:
            result['memory_review_completed'] = False
            result['warnings'].append('Checkpoint saved. Memory review could not run; use memory review to retry it.')
        else:
            result['memory_review_completed'] = True
    return result


def doctor(ws):
    cfg = ws.require()
    errors, warnings = [], []
    for repo in cfg['repositories']:
        try:
            ws.repo_path(repo)
        except Error:
            warnings.append(f'Repository unavailable; rebind before use: {repo}')
    topics, invalid = ws.records('topics')
    errors.extend(invalid)
    topic_ids = {topic['id'] for topic in topics}
    for topic in topics:
        try:
            ws.validate_topic(topic)
        except Error:
            errors.append(f'Topic references unavailable repositories or requirements: {topic["id"]}')
    historical_tasks = 0
    for kind in ('tasks', 'knowledge'):
        records, invalid = ws.records(kind)
        errors.extend(invalid)
        for record in records:
            if kind == 'tasks' and record.get('topic') is not None and record['topic'] not in topic_ids:
                errors.append(f'Task references an unregistered topic: {record["id"]}')
            try:
                body(ws, kind, record['id'])
                if kind == 'tasks' and review_hashes(ws, kind, record) != record.get('reviewed'):
                    if record.get('historical'):
                        historical_tasks += 1
                    else:
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
    if historical_tasks or findings['historical_pending']:
        warnings.append(f'{historical_tasks} historical task(s) and {findings["historical_pending"]} historical memory record(s) retain imported evidence; review on use, not freshly verified.')
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
