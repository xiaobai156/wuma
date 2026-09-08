from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from typing import Callable, Iterator


def _temp_path(path: Path, label: str) -> Path:
    return path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.{label}.tmp"
    )


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = _temp_path(path, "write")
    try:
        with open(temp_path, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def atomic_write_text(path: Path, content: str) -> None:
    normalized = content.replace("\r\n", "\n").replace("\n", os.linesep)
    atomic_write_bytes(path, normalized.encode("utf-8"))


def atomic_write_verified_bytes(
    path: Path,
    content: bytes,
    verify: Callable[[bytes], None],
) -> None:
    original = path.read_bytes() if path.exists() else None
    try:
        atomic_write_bytes(path, content)
        verify(path.read_bytes())
    except BaseException:
        if original is None:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        else:
            atomic_write_bytes(path, original)
        raise


@contextmanager
def file_lock(lock_path: Path, timeout: float = 30.0) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + timeout
        while True:
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"等待事务锁 {lock_path.name} 超时") from exc
                time.sleep(0.1)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _windows_transaction_lock(lock_path: Path, timeout: float = 30.0) -> Iterator[None]:
    """Use a named Windows mutex so output transactions do not create a lock file."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel32.ReleaseMutex.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    digest = hashlib.sha256(
        str(lock_path.resolve()).encode("utf-8")
    ).hexdigest()
    mutex_name = f"Local\\Kill5Transaction-{digest}"
    handle = kernel32.CreateMutexW(None, False, mutex_name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())

    wait_timeout = max(0, min(int(timeout * 1000), 0xFFFFFFFF))
    wait_result = kernel32.WaitForSingleObject(handle, wait_timeout)
    acquired = wait_result in (0x00000000, 0x00000080)
    try:
        if wait_result == 0x00000102:
            raise TimeoutError(f"等待事务锁 {lock_path.name} 超时")
        if wait_result == 0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())
        if not acquired:
            raise OSError(f"等待事务锁 {lock_path.name} 失败：{wait_result}")
        yield
    finally:
        if acquired:
            kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)


@contextmanager
def _transaction_lock(lock_path: Path, timeout: float = 30.0) -> Iterator[None]:
    if os.name == "nt":
        with _windows_transaction_lock(lock_path, timeout):
            yield
    else:
        with file_lock(lock_path, timeout):
            yield


def _transaction_paths(journal_path: Path) -> tuple[Path, Path]:
    backup_dir = journal_path.with_name(f"{journal_path.name}.data")
    lock_path = journal_path.with_name(f"{journal_path.name}.lock")
    return backup_dir, lock_path


def _cleanup_transaction_files(journal_path: Path, backup_dir: Path) -> None:
    try:
        journal_path.unlink()
    except FileNotFoundError:
        pass
    if backup_dir.exists():
        for child in backup_dir.iterdir():
            if child.is_file():
                child.unlink()
            else:
                raise RuntimeError(f"事务备份目录包含异常子目录：{child}")
        backup_dir.rmdir()


def _load_manifest(journal_path: Path) -> dict:
    try:
        manifest = json.loads(journal_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"事务日志损坏，已停止写入：{journal_path}") from exc
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise RuntimeError(f"事务日志版本无效，已停止写入：{journal_path}")
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise RuntimeError(f"事务日志 entries 无效，已停止写入：{journal_path}")
    return manifest


def _restore_transaction(journal_path: Path, backup_dir: Path) -> None:
    manifest = _load_manifest(journal_path)
    errors: list[str] = []
    for index, entry in enumerate(manifest["entries"]):
        try:
            if not isinstance(entry, dict):
                raise ValueError("记录不是对象")
            target = Path(str(entry["path"]))
            existed = entry.get("existed") is True
            backup_name = str(entry.get("backup") or "")
            if existed:
                if Path(backup_name).name != backup_name:
                    raise ValueError("备份文件名无效")
                backup_path = backup_dir / backup_name
                if not backup_path.is_file():
                    raise FileNotFoundError(f"缺少备份 {backup_name}")
                atomic_write_bytes(target, backup_path.read_bytes())
            else:
                try:
                    target.unlink()
                except FileNotFoundError:
                    pass
        except Exception as exc:
            errors.append(f"第 {index + 1} 项恢复失败：{exc}")
    if errors:
        raise RuntimeError("事务回滚未完整完成：" + "；".join(errors))


def _recover_pending_transaction_locked(journal_path: Path) -> bool:
    backup_dir, _lock_path = _transaction_paths(journal_path)
    if not journal_path.exists():
        if backup_dir.exists():
            _cleanup_transaction_files(journal_path, backup_dir)
        return False
    manifest = _load_manifest(journal_path)
    if manifest.get("state") != "committed":
        _restore_transaction(journal_path, backup_dir)
    _cleanup_transaction_files(journal_path, backup_dir)
    return True


def recover_pending_transaction(journal_path: Path) -> bool:
    journal_path = journal_path.resolve()
    _backup_dir, lock_path = _transaction_paths(journal_path)
    with _transaction_lock(lock_path):
        return _recover_pending_transaction_locked(journal_path)


def _snapshot_transaction(paths: list[Path], journal_path: Path) -> Path:
    backup_dir, _lock_path = _transaction_paths(journal_path)
    if backup_dir.exists() or journal_path.exists():
        raise RuntimeError("已有未处理的输出/缓存事务，已停止新提交")
    backup_dir.mkdir(parents=True)
    entries: list[dict] = []
    try:
        for index, path in enumerate(paths):
            existed = path.exists()
            backup_name = f"{index:03d}.bin" if existed else ""
            if existed:
                backup_path = backup_dir / backup_name
                with open(backup_path, "wb") as handle:
                    handle.write(path.read_bytes())
                    handle.flush()
                    os.fsync(handle.fileno())
            entries.append(
                {
                    "path": str(path),
                    "existed": existed,
                    "backup": backup_name,
                }
            )
        manifest = {
            "version": 1,
            "state": "prepared",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "entries": entries,
        }
        atomic_write_bytes(
            journal_path,
            (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )
        return backup_dir
    except Exception:
        _cleanup_transaction_files(journal_path, backup_dir)
        raise


@contextmanager
def file_transaction(paths: list[Path], journal_path: Path) -> Iterator[None]:
    journal_path = journal_path.resolve()
    unique_paths: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = Path(path).resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique_paths.append(resolved)
    _backup_dir, lock_path = _transaction_paths(journal_path)
    with _transaction_lock(lock_path):
        _recover_pending_transaction_locked(journal_path)
        backup_dir = _snapshot_transaction(unique_paths, journal_path)
        try:
            yield
        except BaseException as original:
            try:
                _restore_transaction(journal_path, backup_dir)
                _cleanup_transaction_files(journal_path, backup_dir)
            except Exception as rollback_error:
                raise RuntimeError(
                    f"输出/缓存提交失败且自动回滚未完成：{rollback_error}"
                ) from original
            raise
        else:
            manifest = _load_manifest(journal_path)
            manifest["state"] = "committed"
            atomic_write_bytes(
                journal_path,
                (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            )
            _cleanup_transaction_files(journal_path, backup_dir)


__all__ = [
    "atomic_write_bytes",
    "atomic_write_text",
    "atomic_write_verified_bytes",
    "file_lock",
    "file_transaction",
    "recover_pending_transaction",
]
