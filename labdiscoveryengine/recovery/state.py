"""Durable coordinator records; no dependency on Flask or Redis.

The directory is provisioned outside replaceable deployments. A missing or
incomplete record means closed admission, never a reconstructed ready state.
"""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import tempfile


def read(path):
    with open(path) as stream:
        value = json.load(stream)
    if not isinstance(value, dict) or value.get('version') != 1:
        raise ValueError('Unsupported recovery record')
    return value


def save(path, value):
    path = Path(path)
    if path.is_symlink() or not path.parent.is_dir():
        raise ValueError('Managed recovery directory required')
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(dict(value, version=1), stream, sort_keys=True)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)  # receipts contain identities, never secrets
        os.replace(temporary, path)
        directory = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextlib.contextmanager
def exclusive(path):
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def validate_trusted_path(path, directory=False, owner=0):
    """Reject writable/symlink receipts and replaceable parent directories.

    A root-owned sticky ancestor (e.g. /tmp in isolated tests) is safe for an
    owner-owned child. Deployment state itself must never be group-writable.
    """
    import stat
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('Absolute recovery path required')
    for item in [path, *path.parents]:
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid not in (0, owner):
            raise ValueError('Untrusted recovery path')
        sticky_ancestor = item != path and info.st_uid == 0 and info.st_mode & stat.S_ISVTX
        if info.st_mode & 0o022 and not sticky_ancestor:
            raise ValueError('Writable recovery path')
    info = path.lstat()
    if info.st_uid != owner or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ValueError('Incorrect recovery path owner/type')


def trusted_read(path):
    validate_trusted_path(path)
    return read(path)
