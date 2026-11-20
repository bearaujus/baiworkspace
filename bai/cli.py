"""Command-line interface shared by agents, humans and Make shortcuts."""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import sys
import uuid

from . import __version__
from .core import Error, OpenSetChanged, UNFINISHED_STATES, WAITING_ON, Workspace, open_tasks, read_json
from . import backup, credentials, memory, tooling


def parser():
    p = argparse.ArgumentParser(description='A personal workspace for your preferred AI agent.')
    p.add_argument('--version', action='version', version=__version__)
    p.add_argument('--root', type=Path, default=tooling.PROJECT, help='Clone/workspace root; defaults to this script location.')
    p.add_argument('--json', action='store_true', help='Machine-readable output; secret values are never included.')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('init', help='Initialize ignored personal data, without overwriting existing work.')
    sub.add_parser('doctor', help='Read-only health and prerequisite checks.')
    status = sub.add_parser('status', help='List current unfinished work without resuming it.')
    view = status.add_mutually_exclusive_group()
    view.add_argument('--include-historical', action='store_true')
    view.add_argument('--brief', action='store_true', help='Show the compact current-work brief.')
    c = sub.add_parser('context', help='Read current knowledge, system relationships, tasks and tools.')
    c.add_argument('--query', default='')
    c.add_argument('--repo')
    c.add_argument('--topic', help='Limit the brief to a registered topic ID or alias.')
    c.add_argument('--brief', action='store_true', help='Show the current handoff and relevant supporting references.')
    c.add_argument('--limit', type=int, default=5)
    c.add_argument('--budget', type=int, help='Approximate output character budget, minimum 2000.')

    r = sub.add_parser('repo').add_subparsers(dest='action', required=True)
    r.add_parser('list')
    for action in ('add', 'rebind'):
        x = r.add_parser(action)
        x.add_argument('id')
        x.add_argument('--path', type=Path, required=True)
        if action == 'add':
            x.add_argument('--summary', default='')
            x.add_argument('--aliases', nargs='*', default=[])
    x = r.add_parser('show')
    x.add_argument('id')

    s = sub.add_parser('topic', help='Group work and its references under a reusable topic.').add_subparsers(dest='action', required=True)
    s.add_parser('list')
    x = s.add_parser('add')
    x.add_argument('id')
    x.add_argument('--title', required=True)
    x.add_argument('--purpose', required=True)
    x.add_argument('--aliases', nargs='*', default=[])
    x.add_argument('--reference-repos', nargs='*', default=[])
    x.add_argument('--implementation-repos', nargs='*', default=[])
    x.add_argument('--requirements', nargs='*', default=[], metavar='MEMORY_ID')
    x = s.add_parser('show')
    x.add_argument('id')
    x.add_argument('--history', action='store_true', help='Include closed and historical tasks from this topic.')
    x = s.add_parser('update', help='Merge a non-secret JSON patch after checking the topic revision.')
    x.add_argument('id')
    x.add_argument('--file', type=Path, required=True)
    x.add_argument('--expected-revision', type=int, required=True)

    for command in ('task', 'memory'):
        s = sub.add_parser(command).add_subparsers(dest='action', required=True)
        x = s.add_parser('new' if command == 'task' else 'add')
        x.add_argument('id')
        x.add_argument('--title', required=True)
        x.add_argument('--repos', nargs='*', default=[])
        if command == 'memory':
            x.add_argument('--kind', choices=['learning', 'system', 'procedure', 'preference'], default='learning')
            x.add_argument('--provenance', choices=['user-stated', 'source-verified', 'runtime-observed', 'inference', 'historical-record'], default='user-stated')
        else:
            x.add_argument('--topic', help='Registered topic ID or alias. Requires --expected-open-set.')
            x.add_argument('--done-when', help='What evidence or outcome will establish completion.')
            x.add_argument('--waiting-on', choices=WAITING_ON, help='Responsibility for a waiting task.')
            x.add_argument('--aliases', nargs='*', default=[])
            x.add_argument('--expected-open-set', metavar='TOKEN', help='Open-work token from topic show, task list --open or context --brief.')
        x = s.add_parser('list')
        if command == 'task':
            x.add_argument('--topic', help='List tasks for this registered topic ID or alias.')
            x.add_argument('--open', action='store_true', help='List current open tasks and return an open-set token.')
        x = s.add_parser('show')
        x.add_argument('id')
        x = s.add_parser('update', help='Merge a non-secret JSON patch after checking its revision.')
        x.add_argument('id')
        x.add_argument('--file', type=Path, required=True)
        x.add_argument('--expected-revision', type=int, required=True)
        if command == 'memory':
            x = s.add_parser('review', help='Find changed sources and structural gaps; does not certify truth.')
            x.add_argument('--repo')
            x.add_argument('--include-historical', action='store_true')
            x = s.add_parser('attest', help='After reviewing evidence, record source hashes and refresh the index.')
            x.add_argument('id')
            x.add_argument('--expected-revision', type=int, required=True)
    x = sub.add_parser('checkpoint', help='After reviewing a task, checkpoint its handoff and refresh derived indexes.')
    x.add_argument('id')
    x.add_argument('--expected-revision', type=int, required=True)

    t = sub.add_parser('tools').add_subparsers(dest='action', required=True)
    x = t.add_parser('find')
    x.add_argument('query', nargs='?', default='')
    x = t.add_parser('show')
    x.add_argument('id')
    x = t.add_parser('register')
    x.add_argument('--file', type=Path, required=True)
    x.add_argument('--replace', action='store_true')
    x = t.add_parser('run', help='Run a registered tool; pass tool arguments after --.')
    x.add_argument('id')
    x.add_argument('--profile', default='dev')
    x.add_argument('--execute', action='store_true')
    x.add_argument('--timeout', type=float, default=300)
    x.add_argument('--stdin-file', type=Path, help='Explicit local input file for a noninteractive stdin consumer.')

    s = sub.add_parser('secrets', help='Enter values in your own terminal; never paste them into chat.').add_subparsers(dest='action', required=True)
    for action in ('setup', 'list', 'set', 'remove'):
        x = s.add_parser(action)
        x.add_argument('--profile', default='dev')
        if action == 'setup':
            x.add_argument('--tool', required=True)
        if action in ('set', 'remove'):
            x.add_argument('key')
        if action == 'remove':
            x.add_argument('--yes', action='store_true')

    b = sub.add_parser('backup').add_subparsers(dest='action', required=True)
    x = b.add_parser('create', help='Encrypt full personal data with age; user terminal required.')
    x.add_argument('--output', type=Path)
    x = b.add_parser('restore', help='Restore into an existing clone without .bai; user terminal required.')
    x.add_argument('--file', type=Path, required=True)
    return p


def dispatch(args, tool_args):
    ws = Workspace(args.root)
    command = args.command
    if command == 'init':
        return ws.init(), 0
    if command == 'backup' and args.action == 'restore':
        return backup.restore(ws, args.file), 0
    ws.require()
    if command == 'doctor':
        result = memory.doctor(ws)
        return result, 0 if result['ok'] else 1
    if command == 'status':
        if args.brief:
            return memory.context(ws, 'ongoing work', brief=True), 0
        records, warnings = ws.records('tasks')
        unfinished = [r for r in records if r['status'] in UNFINISHED_STATES]
        return {'tasks': [{k: r[k] for k in ('id', 'title', 'status', 'summary', 'next_step', 'revision')} for r in unfinished if args.include_historical or not r.get('historical')],
                'historical_open_count': sum(bool(r.get('historical')) for r in unfinished), 'warnings': warnings}, 0
    if command == 'context':
        return memory.context(ws, args.query, args.repo, args.limit, args.budget, brief=args.brief, topic=args.topic), 0
    if command == 'repo':
        if args.action in ('add', 'rebind'):
            return ws.register_repo(args.id, args.path, getattr(args, 'summary', ''), getattr(args, 'aliases', []), args.action == 'rebind'), 0
        records = ws.require()['repositories']
        if args.action == 'show':
            name = ws.resolve_repo(args.id)
            if not name:
                raise Error('Repository is not registered.')
            return {'id': name, **records[name], 'brief': f'.bai/repos/{name}.md'}, 0
        return {'repositories': records}, 0
    if command == 'topic':
        if args.action == 'add':
            return ws.create_topic(args.id, args.title, args.purpose, args.aliases,
                                   args.reference_repos, args.implementation_repos, args.requirements), 0
        if args.action == 'list':
            records, warnings = ws.records('topics')
            return {'topics': records, 'warnings': warnings}, 0
        if args.action == 'update':
            return ws.update('topics', args.id, read_json(args.file), args.expected_revision), 0
        name = ws.topic_id(args.id)
        topic = ws.record('topics', name)
        tasks, warnings = ws.records('tasks')
        tasks = sorted((r for r in tasks if r.get('topic') == name), key=lambda r: (r['updated'], r['id']), reverse=True)
        current = open_tasks(tasks, name)
        result = {**topic, 'open_set': ws.open_set(tasks, name), 'tasks': current, 'warnings': warnings}
        if args.history:
            result['history'] = [r for r in tasks if r not in current]
        return result, 0
    if command in ('task', 'memory'):
        kind = 'tasks' if command == 'task' else 'knowledge'
        if args.action in ('new', 'add'):
            if command == 'task':
                return ws.create(kind, args.id, args.title, args.repos, topic=args.topic, done_when=args.done_when,
                                 waiting_on=args.waiting_on, aliases=args.aliases, expected_open_set=args.expected_open_set), 0
            return ws.create(kind, args.id, args.title, args.repos, args.kind, args.provenance), 0
        if args.action == 'show':
            return ws.record(kind, args.id), 0
        if args.action == 'list':
            records, warnings = ws.records(kind)
            result = {'records': records, 'warnings': warnings}
            if command == 'task' and (args.open or args.topic is not None):
                topic = ws.topic_id(args.topic) if args.topic is not None else None
                result['open_set'] = ws.open_set(records, topic)
                if args.open:
                    result['records'] = open_tasks(records, topic)
                else:
                    result['records'] = [r for r in records if r.get('topic') == topic]
            return result, 0
        if args.action == 'update':
            return ws.update(kind, args.id, read_json(args.file), args.expected_revision), 0
        if args.action == 'review':
            repo = ws.resolve_repo(args.repo) if args.repo else None
            if args.repo and not repo:
                raise Error('Repository is not registered.')
            return memory.review(ws, repo, args.include_historical), 0
        if args.action == 'attest':
            return memory.checkpoint(ws, kind, args.id, args.expected_revision), 0
    if command == 'checkpoint':
        return memory.checkpoint(ws, 'tasks', args.id, args.expected_revision), 0
    if command == 'tools':
        if args.action == 'find':
            return tooling.find(ws, args.query), 0
        if args.action == 'show':
            return tooling.inspect_tool(ws, tooling.get(ws, args.id)), 0
        if args.action == 'register':
            return tooling.register(ws, read_json(args.file), args.replace), 0
        if args.action == 'run':
            result = tooling.run(ws, args.id, tool_args, args.profile, args.execute, args.timeout, args.stdin_file)
            return result, result['exit_code'] if result['exit_code'] >= 0 else 128 - result['exit_code']
    if command == 'secrets':
        if args.action == 'list':
            return credentials.listing(ws, args.profile), 0
        if args.action == 'setup':
            return credentials.setup(ws, args.profile, tooling.get(ws, args.tool)['secrets']), 0
        if args.action == 'set':
            return credentials.setup(ws, args.profile, [args.key], update=True), 0
        if args.action == 'remove':
            return credentials.remove(ws, args.profile, args.key, args.yes), 0
    if command == 'backup':
        output = args.output
        if output is None:
            folder = ws.root / 'backups'
            folder.mkdir(exist_ok=True, mode=0o700)
            output = folder / f'baiworkspace-{dt.datetime.now().strftime("%Y%m%d-%H%M%S")}-{uuid.uuid4().hex[:8]}.age'
        return backup.create(ws, output), 0
    raise Error('Unknown command.')


def render(value, indent=0):
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                print(' ' * indent + key + ':')
                render(item, indent + 2)
            else:
                print(' ' * indent + key + ': ' + str(item))
    elif isinstance(value, list):
        if not value:
            print(' ' * indent + '(none)')
        for item in value:
            if isinstance(item, (dict, list)):
                render(item, indent)
                print()
            else:
                print(' ' * indent + '- ' + str(item))
    else:
        print(' ' * indent + str(value))


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    argv = list(sys.argv[1:] if argv is None else argv)
    tail = []
    if '--' in argv:
        index = argv.index('--')
        argv, tail = argv[:index], argv[index + 1:]
    # --json is accepted before or after subcommands, but never consumed from tool args.
    as_json = '--json' in argv
    argv = [x for x in argv if x != '--json']
    if as_json:
        argv.insert(0, '--json')
    p = parser()
    args = p.parse_args(argv)
    if tail and not (args.command == 'tools' and args.action == 'run'):
        p.error('Arguments after -- are only accepted by tools run.')
    try:
        result, code = dispatch(args, tail)
        if as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == 'tools' and args.action == 'run':
            print(result['stdout'], end='')
            print(result['stderr'], end='', file=sys.stderr)
            if code:
                print(f'Exit {code}: {result["outcome"]}. {result["recovery"]}', file=sys.stderr)
            if result['truncated']:
                print('Output display truncated.', file=sys.stderr)
        else:
            render(result)
        return code
    except OpenSetChanged as exc:
        if as_json:
            print(json.dumps({'error': str(exc), **exc.details}, ensure_ascii=False), file=sys.stderr)
        else:
            print('Error: ' + str(exc), file=sys.stderr)
            print(f'Current open tasks ({exc.details["candidate_count"]}):', file=sys.stderr)
            for task in exc.details['candidates']:
                print(f'  {task["id"]} (revision {task["revision"]})', file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        message, code = 'Cancelled; unfinished remote operations may need reconciliation.', 130
    except Error as exc:
        message, code = str(exc), 2
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        message, code = 'Operation failed: check file access and metadata structure. Configuration values suppressed.', 2
    if as_json:
        print(json.dumps({'error': message}), file=sys.stderr)
    else:
        print('Error: ' + message, file=sys.stderr)
    return code
