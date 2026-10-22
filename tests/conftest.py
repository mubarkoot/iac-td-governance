import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tdgov.config import Approver, ApprovalPolicy, Config, LedgerConfig, NotifyConfig  # noqa: E402
from tdgov.governance import Governance  # noqa: E402
from tdgov.ledger import FileLedger  # noqa: E402


@pytest.fixture
def blueprint_dir(tmp_path):
    dst = tmp_path / "blueprint"
    shutil.copytree(ROOT / "blueprint", dst)
    return dst


@pytest.fixture
def cfg(tmp_path, blueprint_dir):
    return Config(
        blueprint_name="test-stack",
        blueprint_dir=str(blueprint_dir),
        base_dir=str(tmp_path),
        ledger=LedgerConfig(backend="file", file_path=str(tmp_path / "ledger.jsonl")),
        approval=ApprovalPolicy(quorum=2, required_roles=["compliance"]),
        approvers=[Approver("alice", "compliance"), Approver("bob", "infrastructure"),
                   Approver("carol", "software-architecture")],
        notify=NotifyConfig(channels=[]),
        scan_unmanaged=False,
        tfvars_file=None,
    )


@pytest.fixture
def ledger(cfg):
    return FileLedger(cfg.ledger.file_path)


@pytest.fixture
def gov(ledger, cfg):
    return Governance(ledger, cfg)
