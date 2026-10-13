"""Reentrant process/thread locks for overlapping hooks sharing a scope."""
from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

_guard = threading.Lock()
_locks: dict[str, threading.RLock] = {}
_local = threading.local()


@contextmanager
def scope_lock(directory: Path):
    key = os.path.normcase(str(directory.resolve()))
    with _guard:
        lock = _locks.setdefault(key, threading.RLock())
    with lock:
        held = getattr(_local, 'held', set())
        if key in held:
            yield
            return
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / '.memory.lock').open('a+b') as handle:
            if os.name == 'nt':
                import msvcrt
                if handle.seek(0, 2) == 0:
                    handle.write(b'\0')
                    handle.flush()

                def acquire():
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

                def release():
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                def acquire():
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)

                def release():
                    fcntl.flock(handle, fcntl.LOCK_UN)

            deadline = time.monotonic() + 2
            while True:
                try:
                    acquire()
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('memory scope is busy')
                    time.sleep(0.01)
            _local.held = held | {key}
            try:
                yield
            finally:
                _local.held = held
                release()
