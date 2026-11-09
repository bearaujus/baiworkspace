"""Portable snapshots encrypted by age; never implement cryptography here."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

from .core import Error, Workspace, contained, digest, encoded, now, read_json, relative

TOP = {'workspace.json', 'bindings.json', 'preferences.md', 'tasks', 'knowledge', 'repos', 'tools', 'private', 'artifacts', 'history'}
MAX_BYTES = 1024 * 1024 * 1024
MAX_FILES = 20000


def snapshot(ws):
    ws.require()
    files = {}
    for path in sorted(ws.data.rglob('*')):
        name = path.relative_to(ws.data).as_posix()
        if name.split('/')[0] == 'cache' or name == 'write.lock' or path.name.startswith('.bai-tmp-'):
            continue
        contained(ws.data, name)
        if name.split('/')[0] not in TOP:
            raise Error('Unexpected workspace content; keep custom files under artifacts or tools before backup.')
        if path.is_dir():
            continue
        if not path.is_file():
            raise Error('Only regular files can be backed up.')
        info = path.stat()
        files[name] = {'sha256': digest(path), 'size': info.st_size, 'executable': bool(info.st_mode & 0o111)}
    if len(files) > MAX_FILES or sum(x['size'] for x in files.values()) > MAX_BYTES:
        raise Error('Workspace exceeds backup limits (20,000 files or 1 GiB).')
    if len({name.casefold() for name in files}) != len(files):
        raise Error('Case-colliding paths are not portable to Windows.')
    return files


def pack(ws, output):
    with ws.lock():
        files = snapshot(ws)
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name in files:
                archive.write(ws.path(name), name)
            archive.writestr('manifest.json', encoded({'schema_version': 1, 'created': now(), 'files': files}))
        # Check the bytes actually archived, including an edit that changed back
        # before the final source snapshot.
        with zipfile.ZipFile(output) as archive:
            for name, expected in files.items():
                hashed = hashlib.sha256()
                with archive.open(name) as stream:
                    while chunk := stream.read(65536):
                        hashed.update(chunk)
                if hashed.hexdigest() != expected['sha256']:
                    raise Error('A file changed while being archived; retry after edits finish.')
        if snapshot(ws) != files:
            raise Error('Workspace changed during backup; retry after concurrent edits finish.')
    return len(files)


def age(mode, source, target):
    executable = shutil.which('age')
    if not executable:
        raise Error('Install age to use encrypted backups. See docs/backup.md.')
    if not sys.stdin.isatty():
        raise Error('Run backup/restore yourself in an interactive terminal for the age passphrase prompt.')
    args = [executable, '-p' if mode == 'encrypt' else '-d', '-o', str(target), str(source)]
    try:
        result = subprocess.run(args, check=False, shell=False)
    except OSError as exc:
        raise Error('Could not start age.') from exc
    if result.returncode:
        raise Error('age failed or was cancelled; no backup or restored workspace was activated.')


def create(ws, output):
    target = Path(output).expanduser().absolute()
    if target.exists() or target.is_symlink():
        raise Error('Backup destination already exists; choose a new filename.')
    if target.resolve().is_relative_to(ws.data.resolve()):
        raise Error('Store backups outside .bai to avoid recursive snapshots.')
    if not target.parent.is_dir():
        raise Error('Backup parent directory does not exist.')
    with tempfile.TemporaryDirectory(prefix='.bai-backup-', dir=target.parent) as directory:
        staging = Path(directory)
        plain, encrypted = staging / 'snapshot.zip', staging / 'snapshot.age'
        # Private staging directory is created with mode 0700 on POSIX.
        count = pack(ws, plain)
        age('encrypt', plain, encrypted)
        if not encrypted.is_file() or not encrypted.stat().st_size:
            raise Error('age did not produce an encrypted backup.')
        # Same-filesystem hard link provides atomic no-replace publication, including Windows.
        try:
            os.link(encrypted, target)
        except FileExistsError as exc:
            raise Error('Backup destination appeared during encryption; nothing overwritten.') from exc
        except OSError as exc:
            raise Error('Cannot publish backup atomically on this filesystem; use a local NTFS/APFS/ext4 directory.') from exc
    return {'backup': str(target), 'files': count, 'encrypted': True, 'credentials_included': True}


def unpack(source, staging):
    """Validate every member before extraction; never use extractall."""
    try:
        with zipfile.ZipFile(source) as archive:
            infos = archive.infolist()
            names = [i.filename for i in infos]
            if len(infos) > MAX_FILES + 1 or len(set(x.casefold() for x in names)) != len(names):
                raise Error('Archive has too many files or duplicate/case-colliding paths.')
            if 'manifest.json' not in names:
                raise Error('Backup manifest is missing.')
            if sum(i.file_size for i in infos) > MAX_BYTES + 8 * 1024 * 1024:
                raise Error('Archive exceeds restore size limit.')
            for info in infos:
                relative(info.filename)
                mode = info.external_attr >> 16
                if info.is_dir() or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG)):
                    raise Error('Backup members must be regular files, never links or devices.')
                if info.filename != 'manifest.json' and info.filename.split('/')[0] not in TOP:
                    raise Error('Archive contains a path outside the personal workspace layout.')
            if archive.getinfo('manifest.json').file_size > 8 * 1024 * 1024:
                raise Error('Backup manifest exceeds size limit.')
            manifest = json.loads(archive.read('manifest.json'))
            if not isinstance(manifest, dict) or manifest.get('schema_version') != 1 or not isinstance(manifest.get('files'), dict):
                raise Error('Unsupported backup version.')
            files = manifest['files']
            if set(files) != set(names) - {'manifest.json'}:
                raise Error('Archive and manifest members differ.')
            for name, expected in files.items():
                if not isinstance(expected, dict) or expected.get('size') != archive.getinfo(name).file_size:
                    raise Error('Archive size does not match its manifest.')
                target = contained(staging, name)
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                hashed = hashlib.sha256()
                with archive.open(name) as src, target.open('xb') as dst:
                    while chunk := src.read(65536):
                        hashed.update(chunk)
                        dst.write(chunk)
                if hashed.hexdigest() != expected.get('sha256'):
                    raise Error('Backup checksum mismatch; nothing restored.')
                target.chmod(0o700 if expected.get('executable') else 0o600)
    except (zipfile.BadZipFile, ValueError, KeyError, OSError, RuntimeError) as exc:
        raise Error('Invalid or corrupt backup archive; nothing restored.') from exc
    for required in ('workspace.json', 'bindings.json', 'preferences.md'):
        if not (staging / required).is_file():
            raise Error('Backup is missing required workspace metadata.')
    cfg = read_json(staging / 'workspace.json')
    if not isinstance(cfg, dict) or cfg.get('schema_version') != 1 or not isinstance(cfg.get('repositories'), dict):
        raise Error('Unsupported restored workspace schema.')
    # Validate portable records and secrets without displaying any values.
    from .core import validate_record
    for kind in ('tasks', 'knowledge'):
        for path in (staging / kind).glob('*.json'):
            record = validate_record(read_json(path), kind)
            if record['id'] != path.stem:
                raise Error('Restored record identity mismatch.')
            if not path.with_suffix('.md').is_file():
                raise Error('Restored record is missing its narrative.')
    # Reuse the normal validators through an isolated view of the staged data.
    staged_ws = Workspace(staging.parent)
    staged_ws.data = staging
    staged_ws.require()
    from .credentials import store
    from .tooling import validate
    store(staged_ws)
    for path in (staging / 'tools').glob('*.json'):
        validate(read_json(path))
    bindings = read_json(staging / 'bindings.json')
    if not isinstance(bindings, dict) or bindings.get('schema_version') != 1 or not isinstance(bindings.get('paths'), dict):
        raise Error('Invalid restored repository bindings.')
    if any(name not in cfg['repositories'] or not isinstance(value, str) for name, value in bindings['paths'].items()):
        raise Error('Invalid restored repository binding values.')
    return manifest


def restore(ws, source):
    source = Path(source).expanduser().resolve()
    if not source.is_file():
        raise Error('Encrypted backup file does not exist.')
    if ws.data.exists() or ws.data.is_symlink():
        raise Error('Restore needs a clone without .bai. Use --root for a separate destination; existing work is never overwritten.')
    if not ws.root.is_dir():
        raise Error('Restore root must be an existing directory.')
    with tempfile.TemporaryDirectory(prefix='.bai-restore-', dir=ws.root) as directory:
        stage = Path(directory)
        plain, data = stage / 'snapshot.zip', stage / 'data'
        data.mkdir(mode=0o700)
        age('decrypt', source, plain)
        unpack(plain, data)
        # Reserve the destination exclusively; publish workspace.json last so commands
        # cannot operate on a partially activated workspace.
        try:
            ws.data.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise Error('Restore destination appeared during decryption; nothing overwritten.') from exc
        try:
            for path in sorted(data.iterdir(), key=lambda p: p.name == 'workspace.json'):
                os.replace(path, ws.data / path.name)
            for name in ('tasks', 'knowledge', 'repos', 'tools', 'private', 'cache', 'artifacts', 'history'):
                ws.path(name).mkdir(exist_ok=True, mode=0o700)
            from .memory import inventory
            from .core import write_json
            write_json(ws.path('cache/index.json'), inventory(ws))
        except BaseException:
            shutil.rmtree(ws.data)
            raise
    missing = []
    for repo in ws.require()['repositories']:
        try:
            ws.repo_path(repo)
        except Error:
            missing.append(repo)
    return {'restored': str(ws.data), 'repos_needing_rebind': missing,
            'next': 'Run doctor and inspect restored tools. No external repositories restored and no tools executed.'}
