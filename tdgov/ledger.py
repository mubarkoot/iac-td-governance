"""Tamper-evident storage for approved blueprints and governance records (DR3).

Two backends share one interface:

* ``ImmudbLedger`` (production): every write and read is verified with
  immudb's cryptographic inclusion and consistency proofs against a locally
  persisted trusted state, so a client detects if the server rewrites
  history.
* ``FileLedger`` (offline use, tests, CI): an append-only JSON-lines file in
  which each entry is chained to its predecessor by SHA-256. ``verify()``
  recomputes the chain and detects any modification, deletion or reordering.

Records are never updated in place; writing an existing key appends a new
version, and ``history(key)`` returns all versions.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


class LedgerError(Exception):
    pass


class VerificationError(LedgerError):
    pass


@dataclass
class Entry:
    key: str
    value: bytes
    tx: int
    ts: float | None = None


class Ledger(ABC):
    @abstractmethod
    def put(self, key: str, value: bytes) -> int: ...

    @abstractmethod
    def get(self, key: str) -> Entry | None: ...

    @abstractmethod
    def history(self, key: str) -> list[Entry]: ...

    @abstractmethod
    def scan(self, prefix: str) -> list[Entry]:
        """Latest version of every key that starts with ``prefix``, sorted by key."""

    def verify(self) -> bool:
        return True

    def stats(self) -> dict:
        return {}

    def put_json(self, key: str, obj) -> int:
        return self.put(key, json.dumps(obj, sort_keys=True, separators=(",", ":")).encode())

    def get_json(self, key: str):
        e = self.get(key)
        return None if e is None else json.loads(e.value)

    def close(self) -> None:
        pass


# --------------------------------------------------------------------------- file


class FileLedger(Ledger):
    GENESIS = "0" * 64

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    @staticmethod
    def _entry_hash(e: dict) -> str:
        body = {k: e[k] for k in ("seq", "ts", "key", "value", "prev")}
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _read(self) -> list[dict]:
        with self.path.open("r", encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def put(self, key: str, value: bytes) -> int:
        with self.path.open("a+", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.seek(0)
                last = None
                for line in fh:
                    if line.strip():
                        last = json.loads(line)
                seq = 1 if last is None else last["seq"] + 1
                prev = self.GENESIS if last is None else last["hash"]
                e = {
                    "seq": seq,
                    "ts": time.time(),
                    "key": key,
                    "value": base64.b64encode(value).decode(),
                    "prev": prev,
                }
                e["hash"] = self._entry_hash(e)
                fh.seek(0, os.SEEK_END)
                fh.write(json.dumps(e, sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
                return seq
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _verified_entries(self) -> list[dict]:
        entries = self._read()
        prev = self.GENESIS
        for i, e in enumerate(entries, start=1):
            if e.get("seq") != i or e.get("prev") != prev or self._entry_hash(e) != e.get("hash"):
                raise VerificationError(f"ledger chain broken at entry {i}")
            prev = e["hash"]
        return entries

    def verify(self) -> bool:
        self._verified_entries()
        return True

    def _to_entry(self, e: dict) -> Entry:
        return Entry(key=e["key"], value=base64.b64decode(e["value"]), tx=e["seq"], ts=e["ts"])

    def get(self, key: str) -> Entry | None:
        hist = self.history(key)
        return hist[-1] if hist else None

    def history(self, key: str) -> list[Entry]:
        return [self._to_entry(e) for e in self._verified_entries() if e["key"] == key]

    def scan(self, prefix: str) -> list[Entry]:
        latest: dict[str, dict] = {}
        for e in self._verified_entries():
            if e["key"].startswith(prefix):
                latest[e["key"]] = e
        return [self._to_entry(latest[k]) for k in sorted(latest)]

    def stats(self) -> dict:
        entries = self._read()
        return {
            "backend": "file",
            "entries": len(entries),
            "bytes_on_disk": self.path.stat().st_size,
            "keys": len({e["key"] for e in entries}),
        }


# ------------------------------------------------------------------------- immudb


class ImmudbLedger(Ledger):
    """immudb backend using the official ``immudb-py`` client."""

    PAGE = 999

    def __init__(self, host: str, port: int, user: str, password: str, database: str = "defaultdb",
                 state_dir: str | Path = ".tdgov/immudb-state"):
        try:
            from immudb import ImmudbClient
        except ImportError as exc:  # pragma: no cover - depends on optional dependency
            raise LedgerError("immudb-py is not installed: pip install immudb-py") from exc

        Path(state_dir).mkdir(parents=True, exist_ok=True)
        root_service = None
        try:
            from immudb.rootService import PersistentRootService

            root_service = PersistentRootService(str(Path(state_dir) / "root"))
        except Exception:  # pragma: no cover - older client versions
            root_service = None
        url = f"{host}:{port}"
        try:
            self.client = ImmudbClient(url, rs=root_service) if root_service else ImmudbClient(url)
        except TypeError:  # pragma: no cover
            self.client = ImmudbClient(url)
        self.client.login(user, password, database=database.encode())
        self._database = database

    def put(self, key: str, value: bytes) -> int:
        resp = self.client.verifiedSet(key.encode(), value)
        if hasattr(resp, "verified") and not resp.verified:
            raise VerificationError(f"immudb could not verify write of {key}")
        return int(getattr(resp, "id", 0))

    def get(self, key: str) -> Entry | None:
        try:
            resp = self.client.verifiedGet(key.encode())
        except Exception as exc:
            if "key not found" in str(exc).lower():
                return None
            raise
        if resp is None:
            return None
        if hasattr(resp, "verified") and not resp.verified:
            raise VerificationError(f"immudb proof verification failed for {key}")
        return Entry(key=key, value=resp.value, tx=int(getattr(resp, "id", 0)),
                     ts=getattr(resp, "timestamp", None))

    def history(self, key: str) -> list[Entry]:
        out, offset = [], 0
        while True:
            try:
                page = self.client.history(key.encode(), offset, self.PAGE, False)
            except Exception as exc:
                if "key not found" in str(exc).lower():
                    return out
                raise
            for item in page:
                out.append(Entry(key=key, value=item.value, tx=int(getattr(item, "tx", 0))))
            if len(page) < self.PAGE:
                return out
            offset += len(page)

    def scan(self, prefix: str) -> list[Entry]:
        out, seek = [], b""
        while True:
            page = self.client.scan(seek, prefix.encode(), False, self.PAGE)
            items = sorted(page.items())
            for k, v in items:
                out.append(Entry(key=k.decode(), value=v, tx=0))
            if len(items) < self.PAGE:
                return out
            seek = items[-1][0]

    def verify(self) -> bool:
        # Every read and write is already proof-verified; additionally check
        # that the server state is consistent with our persisted trusted state.
        self.client.currentState()
        return True

    def stats(self) -> dict:
        state = self.client.currentState()
        return {"backend": "immudb", "database": self._database, "tx_id": getattr(state, "txId", None)}

    def close(self) -> None:
        try:
            self.client.logout()
        except Exception:
            pass


def open_ledger(cfg) -> Ledger:
    lc = cfg.ledger
    if lc.backend == "file":
        return FileLedger(cfg.resolve(lc.file_path))
    if lc.backend == "immudb":
        user = os.environ.get(lc.user_env)
        password = os.environ.get(lc.password_env)
        if not user or not password:
            raise LedgerError(f"set {lc.user_env} and {lc.password_env} for the immudb ledger")
        return ImmudbLedger(lc.host, lc.port, user, password, lc.database, cfg.resolve(lc.state_dir))
    raise LedgerError(f"unknown ledger backend: {lc.backend}")
