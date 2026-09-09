"""OS-protected secrets. Native calls only: no secrets in subprocess arguments or logs."""

from __future__ import annotations

import ctypes as c
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

SERVICE = b"de.lernapp.credentials.v1"
KEY_LINE = re.compile(r"^\s*(?:export\s+)?CREDENTIAL_ENCRYPTION_KEY\s*=.*(?:\n|$)", re.M)


class StorageError(RuntimeError):
    pass


def supported() -> bool:
    return sys.platform in ("darwin", "win32")


def account(root: Path, suffix: str = "master") -> str:
    return hashlib.sha256((str(root.resolve()) + ":" + suffix).encode()).hexdigest()


def _mac(action: str, name: str, value: bytes | None = None) -> bytes | None:
    security = c.CDLL("/System/Library/Frameworks/Security.framework/Security")
    cf = c.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    ptr = c.c_void_p
    cf.CFStringCreateWithCString.argtypes = [ptr, c.c_char_p, c.c_uint32]
    cf.CFStringCreateWithCString.restype = ptr
    cf.CFDictionaryCreateMutable.argtypes = [ptr, c.c_long, ptr, ptr]
    cf.CFDictionaryCreateMutable.restype = ptr
    cf.CFDictionarySetValue.argtypes = [ptr, ptr, ptr]
    cf.CFDataCreate.argtypes = [ptr, ptr, c.c_long]
    cf.CFDataCreate.restype = ptr
    cf.CFDataGetLength.argtypes = [ptr]
    cf.CFDataGetLength.restype = c.c_long
    cf.CFDataGetBytePtr.argtypes = [ptr]
    cf.CFDataGetBytePtr.restype = ptr
    cf.CFRelease.argtypes = [ptr]
    security.SecItemCopyMatching.argtypes = [ptr, c.POINTER(ptr)]
    security.SecItemCopyMatching.restype = c.c_int32
    security.SecItemAdd.argtypes = [ptr, ptr]
    security.SecItemAdd.restype = c.c_int32
    security.SecItemDelete.argtypes = [ptr]
    security.SecItemDelete.restype = c.c_int32
    allocated: list[Any] = []
    query = cf.CFDictionaryCreateMutable(None, 0, None, None)
    allocated.append(query)

    def constant(lib: Any, label: str) -> Any:
        return ptr.in_dll(lib, label)

    def string(value: bytes) -> Any:
        ref = cf.CFStringCreateWithCString(None, value, 0x08000100)
        allocated.append(ref)
        return ref

    def set_value(key: str, val: Any) -> None:
        cf.CFDictionarySetValue(query, constant(security, key), val)

    try:
        set_value("kSecClass", constant(security, "kSecClassGenericPassword"))
        set_value("kSecAttrService", string(SERVICE))
        set_value("kSecAttrAccount", string(name.encode()))
        set_value("kSecAttrSynchronizable", constant(cf, "kCFBooleanFalse"))
        if action == "read":
            set_value("kSecReturnData", constant(cf, "kCFBooleanTrue"))
            result = ptr()
            status = security.SecItemCopyMatching(query, c.byref(result))
            if status == -25300:
                return None
            if status == 0:
                try:
                    return c.string_at(cf.CFDataGetBytePtr(result), cf.CFDataGetLength(result))
                finally:
                    cf.CFRelease(result)
        elif action == "write":
            assert value is not None
            data = cf.CFDataCreate(None, c.c_char_p(value), len(value))
            allocated.append(data)
            set_value("kSecValueData", data)
            status = security.SecItemAdd(query, None)
        elif action == "delete":
            status = security.SecItemDelete(query)
            if status == -25300:
                return None
        else:
            raise ValueError("Unsupported keychain operation")
        if status != 0:
            raise StorageError(
                "macOS-Schlüsselbund nicht verfügbar. Bitte den Anmeldeschlüsselbund entsperren und erneut versuchen."
            )
        return None
    finally:
        for ref in reversed(allocated):
            cf.CFRelease(ref)


def _dpapi(value: bytes, *, decrypt: bool = False) -> bytes:
    class Blob(c.Structure):
        _fields_ = [("size", c.c_uint32), ("data", c.c_void_p)]

    dll: Any = c.__dict__["WinDLL"]("crypt32", use_last_error=True)
    kernel: Any = c.__dict__["WinDLL"]("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [c.c_void_p]
    kernel.LocalFree.restype = c.c_void_p
    buffer = c.create_string_buffer(value)
    source, target = Blob(len(value), c.cast(buffer, c.c_void_p)), Blob()
    fn = dll.CryptUnprotectData if decrypt else dll.CryptProtectData
    fn.argtypes = [c.POINTER(Blob), c.c_void_p, c.c_void_p, c.c_void_p, c.c_void_p, c.c_uint32, c.POINTER(Blob)]
    fn.restype = c.c_int
    # User scope: never CRYPTPROTECT_LOCAL_MACHINE. No interactive prompt in the backend.
    if not fn(c.byref(source), None, None, None, None, 1, c.byref(target)):
        raise StorageError(
            "Windows-Schlüsselschutz nicht verfügbar. Bitte mit dem ursprünglichen Windows-Konto anmelden."
        )
    try:
        return c.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


def _path(root: Path, name: str) -> Path:
    folder = root / "os-secrets"
    created = not folder.exists()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if created and sys.platform == "win32":
        import csv
        import subprocess

        system = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32"
        result = subprocess.run(
            [str(system / "whoami.exe"), "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        sid = next(csv.reader(result.stdout.splitlines()))[1]
        if not re.fullmatch(r"S-1-[0-9-]+", sid):
            raise StorageError("Windows-Benutzerrechte konnten nicht geprüft werden.")
        subprocess.run(
            [
                str(system / "icacls.exe"),
                str(folder),
                "/inheritance:r",
                "/grant:r",
                f"*{sid}:(OI)(CI)F",
                "*S-1-5-18:(OI)(CI)F",
            ],
            capture_output=True,
            check=True,
            timeout=10,
        )
    return folder / (name + ".dpapi")


def read(root: Path, suffix: str = "master") -> bytes | None:
    name = account(root, suffix)
    if sys.platform == "darwin":
        return _mac("read", name)
    if sys.platform == "win32":
        path = _path(root, name)
        return _dpapi(path.read_bytes(), decrypt=True) if path.exists() else None
    raise StorageError("OS-Schlüsselschutz ist auf diesem System nicht eingerichtet.")


def write(root: Path, value: bytes, suffix: str = "master") -> None:
    existing = read(root, suffix)
    if existing is not None:
        if existing != value:
            raise StorageError(
                "Der vorhandene Geräteschlüssel stimmt nicht überein. Es wurden keine Schlüssel ersetzt."
            )
        return
    name = account(root, suffix)
    if sys.platform == "darwin":
        _mac("write", name, value)
    elif sys.platform == "win32":
        encrypted = _dpapi(value)
        with _path(root, name).open("xb") as handle:
            handle.write(encrypted)
            handle.flush()
            os.fsync(handle.fileno())
    else:
        raise StorageError("OS-Schlüsselschutz ist auf diesem System nicht eingerichtet.")
    if read(root, suffix) != value:
        raise StorageError("Der gespeicherte Geräteschlüssel konnte nicht geprüft werden.")


def delete(root: Path, suffix: str = "master") -> None:
    if sys.platform == "darwin":
        _mac("delete", account(root, suffix))
    elif sys.platform == "win32":
        _path(root, account(root, suffix)).unlink(missing_ok=True)


def strip_legacy(path: Path) -> None:
    if not path.exists():
        return
    content = path.read_text(encoding="utf-8")
    cleaned = KEY_LINE.sub("", content)
    if cleaned == content:
        return
    temporary = path.with_name(path.name + ".secure-tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        os.chmod(temporary, 0o600)
        handle.write(cleaned)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def protect_legacy_backups(root: Path) -> int:
    """Run after successful upgrade, so an in-flight pre-migration rollback stays usable."""
    if read(root) is None:
        raise StorageError("Kein Geräteschlüssel vorhanden; Sicherungen bleiben unverändert.")
    paths = list((root / "updates").glob("version-*/backup-data/.env"))
    paths += list((root / "backups").rglob(".env"))
    count = 0
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            continue
        content = path.read_bytes()
        if not KEY_LINE.search(content.decode("utf-8")):
            continue
        suffix = "backup:" + str(path.relative_to(root))
        write(root, content, suffix)
        marker = path.with_name(".env.os-protected")
        marker.write_text(json.dumps({"version": 1, "suffix": suffix}), encoding="utf-8")
        # Protect the complete original environment, including other legacy secret fields.
        temporary = path.with_name(".env.secure-tmp")
        temporary.write_text("# Original configuration is protected in the OS credential store.\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(path)
        count += 1
    return count


def backup_env(root: Path, folder: Path) -> bytes | None:
    marker = folder / ".env.os-protected"
    if marker.exists():
        data = json.loads(marker.read_text(encoding="utf-8"))
        if data.get("version") != 1 or not str(data.get("suffix", "")).startswith("backup:"):
            raise StorageError("Ungültiger Sicherungsverweis.")
        value = read(root, data["suffix"])
        if value is None:
            raise StorageError("Diese Sicherung benötigt den ursprünglichen OS-Schlüsselspeicher.")
        return value
    path = folder / ".env"
    return path.read_bytes() if path.exists() else None


def finish_backup_migration(root: Path) -> None:
    """Wait for a running updater to commit; never change its rollback inputs early."""
    import logging
    import time

    try:
        for _ in range(900):
            if not (root / "updates/install.lock").exists():
                count = protect_legacy_backups(root)
                logging.getLogger(__name__).info("OS-protected %d legacy configuration backups", count)
                return
            time.sleep(2)
        logging.getLogger(__name__).warning("Backup key migration deferred until the next app start")
    except Exception as exc:
        logging.getLogger(__name__).warning("Backup key migration deferred (%s)", type(exc).__name__)
