from datetime import datetime, timedelta, timezone

import pytest

from tdgov import canonical
from tdgov.governance import GovernanceError


def _submit(gov, blueprint_dir, author="dave", msg="initial"):
    return gov.submit(canonical.bundle(blueprint_dir), author=author, message=msg)


def test_quorum_and_required_role(gov, blueprint_dir):
    cr = _submit(gov, blueprint_dir)
    out = gov.approve(cr["cr_id"], "bob")
    assert not out["policy"]["satisfied"] and out["policy"]["missing_roles"] == ["compliance"]
    assert gov.approved_baseline() is None
    out = gov.approve(cr["cr_id"], "carol")
    assert not out["policy"]["satisfied"]  # quorum met, compliance role still missing
    out = gov.approve(cr["cr_id"], "alice")
    assert out["policy"]["satisfied"]
    base = gov.approved_baseline()
    assert base["version"] == 1 and base["digest"] == cr["digest"]
    assert gov.get_cr(cr["cr_id"])["status"] == "approved"


def test_self_approval_and_unknown_approver_rejected(gov, blueprint_dir):
    cr = _submit(gov, blueprint_dir, author="alice")
    with pytest.raises(GovernanceError):
        gov.approve(cr["cr_id"], "alice")
    with pytest.raises(GovernanceError):
        gov.approve(cr["cr_id"], "mallory")


def test_baseline_history_and_stale_cr(gov, blueprint_dir):
    cr1 = _submit(gov, blueprint_dir)
    (blueprint_dir / "main.tf").write_text((blueprint_dir / "main.tf").read_text() + "\n# v2\n")
    cr2 = _submit(gov, blueprint_dir, msg="v2")
    for a in ("alice", "bob"):
        gov.approve(cr1["cr_id"], a)
    # cr2 was based on "no baseline"; the baseline has changed since
    with pytest.raises(GovernanceError):
        gov.approve(cr2["cr_id"], "alice")
    cr3 = _submit(gov, blueprint_dir, msg="v2 rebased")
    for a in ("alice", "bob"):
        gov.approve(cr3["cr_id"], a)
    hist = gov.baseline_history()
    assert [h["version"] for h in hist] == [1, 2]
    assert hist[1]["previous_digest"] == hist[0]["digest"]


def test_identical_submission_rejected(gov, blueprint_dir):
    cr = _submit(gov, blueprint_dir)
    gov.approve(cr["cr_id"], "alice")
    gov.approve(cr["cr_id"], "bob")
    with pytest.raises(GovernanceError):
        _submit(gov, blueprint_dir)


def test_bundle_integrity_on_load(gov, blueprint_dir):
    cr = _submit(gov, blueprint_dir)
    assert canonical.digest_of(gov.load_bundle(cr["digest"])) == cr["digest"]
    with pytest.raises(GovernanceError):
        gov.load_bundle("f" * 64)


def test_reject(gov, blueprint_dir):
    cr = _submit(gov, blueprint_dir)
    gov.reject(cr["cr_id"], "alice", "uses deprecated image")
    with pytest.raises(GovernanceError):
        gov.approve(cr["cr_id"], "bob")
    assert gov.list_crs("rejected")[0]["reason"] == "uses deprecated image"


def test_breakglass_window(gov):
    assert gov.breakglass_active() is None
    rec = gov.breakglass_open("oncall", "restore service after outage", ttl_minutes=30)
    assert gov.breakglass_active()["actor"] == "oncall"
    later = datetime.fromisoformat(rec["expires_at"]) + timedelta(seconds=1)
    assert gov.breakglass_active(at=later) is None
    gov.breakglass_close("oncall", "reverted")
    assert gov.breakglass_active() is None
    with pytest.raises(GovernanceError):
        gov.breakglass_open("x", "  ")
    kinds = [e["kind"] for e in gov.events()]
    assert kinds == ["breakglass-opened", "breakglass-closed"]
    assert gov.events(since=datetime.now(timezone.utc) + timedelta(hours=1)) == []
