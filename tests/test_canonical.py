import os
import time

import pytest

from tdgov import canonical


def test_bundle_is_deterministic(blueprint_dir):
    a = canonical.bundle(blueprint_dir)
    # touching files (new mtimes) must not change the digest
    time.sleep(1.1)
    for p in blueprint_dir.rglob("*.tf"):
        os.utime(p)
    b = canonical.bundle(blueprint_dir)
    assert a.digest == b.digest and a.data == b.data


def test_line_endings_normalized(blueprint_dir):
    a = canonical.bundle(blueprint_dir)
    p = blueprint_dir / "outputs.tf"
    p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
    assert canonical.bundle(blueprint_dir).digest == a.digest


def test_tfvars_and_state_excluded(blueprint_dir):
    (blueprint_dir / "terraform.tfvars").write_text('db_admin_password = "secret"\n')
    (blueprint_dir / "terraform.tfstate").write_text("{}")
    (blueprint_dir / ".terraform").mkdir()
    (blueprint_dir / ".terraform" / "x.tf").write_text("")
    files = canonical.collect(blueprint_dir)
    assert not any(n.endswith((".tfvars", ".tfstate", ".example")) or n.startswith(".terraform/") for n in files)
    assert "main.tf" in files and "templates/db.yaml.tftpl" in files


def test_content_change_changes_digest(blueprint_dir):
    a = canonical.bundle(blueprint_dir)
    (blueprint_dir / "main.tf").write_text((blueprint_dir / "main.tf").read_text() + "\n# change\n")
    b = canonical.bundle(blueprint_dir)
    assert a.digest != b.digest
    assert canonical.diff_manifests(a.manifest, b.manifest)["modified"] == ["main.tf"]


def test_materialize_roundtrip_and_tamper_detection(blueprint_dir, tmp_path):
    b = canonical.bundle(blueprint_dir)
    out = tmp_path / "out"
    canonical.materialize(b.data, out, expected_digest=b.digest)
    assert (out / "main.tf").read_text() == (blueprint_dir / "main.tf").read_text()
    with pytest.raises(ValueError):
        canonical.materialize(b.data, tmp_path / "x", expected_digest="0" * 64)
