"""Make convenience adapter; values travel as argv, never shell interpolation."""
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bai.cli import main

target = sys.argv[1]
profile = os.environ.get('PROFILE') or 'dev'
commands = {
    'help': ['--help'], 'init': ['init'], 'doctor': ['doctor'], 'status': ['status'],
    'context': ['context', '--query', os.environ.get('QUERY', '')],
    'secrets-setup': ['secrets', 'setup', '--tool', os.environ.get('TOOL', ''), '--profile', profile],
    'secrets-list': ['secrets', 'list', '--profile', profile],
    'secret-set': ['secrets', 'set', os.environ.get('KEY', ''), '--profile', profile],
    'secret-remove': ['secrets', 'remove', os.environ.get('KEY', ''), '--profile', profile],
    'backup': ['backup', 'create'],
    'restore-backup': ['backup', 'restore', '--file', os.environ.get('FILE', '')],
}
args = commands[target]
if target == 'backup' and os.environ.get('OUT'):
    args += ['--output', os.environ['OUT']]
if target == 'context' and os.environ.get('REPO'):
    args += ['--repo', os.environ['REPO']]
sys.exit(main(args))
