from __future__ import annotations

import copy
import json
import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")

_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()
_LOCK_DEPTHS = threading.local()


def _lock_key(lock_path: Path) -> str:
    return str(lock_path.resolve(strict=False))


def _thread_lock(lock_path: Path) -> threading.RLock:
    key = _lock_key(lock_path)
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _THREAD_LOCKS[key] = lock
        return lock


def _lock_depths() -> dict[str, int]:
    depths = getattr(_LOCK_DEPTHS, "depths", None)
    if depths is None:
        depths = {}
        _LOCK_DEPTHS.depths = depths
    return depths


def _enter_reentrant_lock(key: str) -> bool:
    depths = _lock_depths()
    depth = depths.get(key, 0)
    if depth == 0:
        return False
    depths[key] = depth + 1
    return True


def _exit_reentrant_lock(key: str) -> None:
    depths = _lock_depths()
    depth = depths[key]
    if depth == 1:
        depths.pop(key)
    else:
        depths[key] = depth - 1


@contextmanager
def opencli_state_lock(path: Path) -> Iterator[None]:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f"{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    key = _lock_key(lock_path)
    with _thread_lock(lock_path):
        if _enter_reentrant_lock(key):
            try:
                yield
            finally:
                _exit_reentrant_lock(key)
            return
        if os.name == "posix":
            import fcntl

            with lock_path.open("a+b") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                _lock_depths()[key] = 1
                try:
                    yield
                finally:
                    _exit_reentrant_lock(key)
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            return
        if os.name == "nt":
            import msvcrt

            with lock_path.open("a+b") as lock_file:
                lock_file.seek(0, os.SEEK_END)
                if lock_file.tell() == 0:
                    lock_file.write(b"\0")
                    lock_file.flush()
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                _lock_depths()[key] = 1
                try:
                    yield
                finally:
                    _exit_reentrant_lock(key)
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            return
        raise RuntimeError(f"unsupported OS for OpenCLI local state lock: {os.name}")


def locked_json_update(path: Path, default: T, update: Callable[[T], T]) -> T:
    path = Path(path)
    with opencli_state_lock(path):
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            current = copy.deepcopy(default)
        next_value = update(current)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            tmp.write_text(json.dumps(next_value, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
        return next_value
