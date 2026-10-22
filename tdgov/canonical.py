"""Canonical serialization of a blueprint directory (Step 3).

The paper notes that serialization "into a canonical byte representation is
crucial for verifying integrity, ensuring reproducibility, and supporting
auditability". This module produces a deterministic archive: the same
blueprint content always yields the same bytes and therefore the same
SHA-256 digest, independent of file timestamps, ownership or ordering.

Only governed files are included (HCL, templates, the provider lock file).
Variable files (``*.tfvars``), state, plans and ``.terraform/`` are excluded,
because they are environment-specific or may contain secrets.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from dataclasses import dataclass
from pathlib import Path

INCLUDE_SUFFIXES = (".tf", ".tftpl", ".hcl", ".tf.json")
INCLUDE_NAMES = (".terraform.lock.hcl",)
EXCLUDE_DIRS = {".terraform", ".git", "__pycache__"}
EXCLUDE_SUFFIXES = (".tfvars", ".tfvars.json", ".tfstate", ".tfstate.backup", ".tfplan")


@dataclass(frozen=True)
class Bundle:
    data: bytes          # deterministic .tar.gz
    digest: str          # sha256 hex of `data`
    manifest: dict       # relative path -> sha256 of file content

    @property
    def size(self) -> int:
        return len(self.data)


def _governed(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    if any(part in EXCLUDE_DIRS for part in rel.parts):
        return False
    name = path.name
    if name.endswith(EXCLUDE_SUFFIXES) or name.endswith(".example"):
        return False
    return name in INCLUDE_NAMES or name.endswith(INCLUDE_SUFFIXES)


def _normalize(content: bytes) -> bytes:
    # Normalize line endings so that checkouts on different OSes hash equally.
    return content.replace(b"\r\n", b"\n")


def collect(root: str | Path) -> dict[str, bytes]:
    root = Path(root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"blueprint directory not found: {root}")
    files = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and _governed(p, root):
            files[p.relative_to(root).as_posix()] = _normalize(p.read_bytes())
    if not files:
        raise ValueError(f"no governed blueprint files found in {root}")
    return files


def bundle(root: str | Path) -> Bundle:
    files = collect(root)
    manifest = {name: hashlib.sha256(c).hexdigest() for name, c in files.items()}
    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        # Manifest first, then files in sorted order.
        entries = [("MANIFEST.json", json.dumps(manifest, sort_keys=True, indent=2).encode())]
        entries += sorted(files.items())
        for name, content in entries:
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(content))
    gz_buf = io.BytesIO()
    with gzip.GzipFile(fileobj=gz_buf, mode="wb", mtime=0, compresslevel=9) as gz:
        gz.write(tar_buf.getvalue())
    data = gz_buf.getvalue()
    return Bundle(data=data, digest=hashlib.sha256(data).hexdigest(), manifest=manifest)


def digest_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def materialize(data: bytes, target: str | Path, expected_digest: str | None = None) -> dict:
    """Extract a bundle into ``target`` after verifying its digest and manifest."""
    if expected_digest is not None and digest_of(data) != expected_digest:
        raise ValueError("bundle digest mismatch: refusing to materialize a modified blueprint")
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(data)), mode="r:") as tar:
        members = tar.getmembers()
        manifest = json.loads(tar.extractfile("MANIFEST.json").read())
        for m in members:
            if m.name == "MANIFEST.json":
                continue
            dest = (target / m.name).resolve()
            if not str(dest).startswith(str(target.resolve())):
                raise ValueError(f"unsafe path in bundle: {m.name}")
            content = tar.extractfile(m).read()
            if hashlib.sha256(content).hexdigest() != manifest.get(m.name):
                raise ValueError(f"manifest mismatch for {m.name}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
    return manifest


def diff_manifests(old: dict, new: dict) -> dict[str, list[str]]:
    return {
        "added": sorted(set(new) - set(old)),
        "removed": sorted(set(old) - set(new)),
        "modified": sorted(k for k in set(old) & set(new) if old[k] != new[k]),
    }
