"""Configuration loading.

Configuration is read from a YAML file (default: ``tdgov.yaml`` in the
working directory, or the path in ``TDGOV_CONFIG``). Secrets are never read
from this file; they come from the environment or the secrets manager.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(Exception):
    pass


@dataclass
class Approver:
    id: str
    role: str
    email: str | None = None


@dataclass
class ApprovalPolicy:
    quorum: int = 2
    required_roles: list[str] = field(default_factory=lambda: ["compliance"])
    allow_self_approval: bool = False


@dataclass
class LedgerConfig:
    backend: str = "immudb"  # "immudb" or "file"
    host: str = "127.0.0.1"
    port: int = 3322
    database: str = "defaultdb"
    user_env: str = "IMMUDB_USER"
    password_env: str = "IMMUDB_PASSWORD"
    state_dir: str = ".tdgov/immudb-state"
    file_path: str = ".tdgov/ledger.jsonl"


@dataclass
class NotifyConfig:
    channels: list[str] = field(default_factory=lambda: ["stdout"])
    webhook_url_env: str = "TDGOV_WEBHOOK_URL"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user_env: str = "TDGOV_SMTP_USER"
    smtp_password_env: str = "TDGOV_SMTP_PASSWORD"
    email_from: str | None = None
    email_to: list[str] = field(default_factory=list)


@dataclass
class SecretsConfig:
    provider: str = "env"  # "env" or "vault"
    # Maps Terraform variable name -> secret reference.
    #   env:   name of the environment variable
    #   vault: "<kv-v2 path>#<field>", e.g. "secret/data/tdgov/db#admin_password"
    mapping: dict[str, str] = field(default_factory=dict)
    vault_addr_env: str = "VAULT_ADDR"
    vault_token_env: str = "VAULT_TOKEN"


@dataclass
class ProxmoxConfig:
    endpoint_env: str = "PROXMOX_VE_ENDPOINT"
    token_env: str = "PROXMOX_VE_API_TOKEN"
    verify_tls: bool = True
    ignore_tag: str = "tdgov-ignore"


@dataclass
class Config:
    blueprint_name: str = "reference-web-stack"
    blueprint_dir: str = "blueprint"
    policy_dir: str = "policies"
    policy_data_dir: str = "policies/data"
    tfvars_file: str | None = "blueprint/terraform.tfvars"
    backend_config: dict[str, str] = field(
        default_factory=lambda: {"path": ".tdgov/state/terraform.tfstate"}
    )
    work_dir: str = ".tdgov/work"
    terraform_bin: str = "terraform"
    conftest_bin: str = "conftest"
    ledger: LedgerConfig = field(default_factory=LedgerConfig)
    approval: ApprovalPolicy = field(default_factory=ApprovalPolicy)
    approvers: list[Approver] = field(default_factory=list)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    secrets: SecretsConfig = field(default_factory=SecretsConfig)
    proxmox: ProxmoxConfig = field(default_factory=ProxmoxConfig)
    scan_unmanaged: bool = True
    unmanaged_vmid_range: list[int] | None = None

    def approver(self, approver_id: str) -> Approver | None:
        for a in self.approvers:
            if a.id == approver_id:
                return a
        return None

    def resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else Path(self.base_dir) / p

    base_dir: str = "."


def _build(cls, data: dict[str, Any] | None):
    data = data or {}
    known = {f for f in cls.__dataclass_fields__}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"unknown keys for {cls.__name__}: {sorted(unknown)}")
    return cls(**data)


def load_config(path: str | os.PathLike | None = None) -> Config:
    path = Path(path or os.environ.get("TDGOV_CONFIG", "tdgov.yaml"))
    if not path.exists():
        raise ConfigError(f"configuration file not found: {path}")
    raw = yaml.safe_load(path.read_text()) or {}
    try:
        cfg = Config(
            blueprint_name=raw.get("blueprint_name", "reference-web-stack"),
            blueprint_dir=raw.get("blueprint_dir", "blueprint"),
            policy_dir=raw.get("policy_dir", "policies"),
            policy_data_dir=raw.get("policy_data_dir", "policies/data"),
            tfvars_file=raw.get("tfvars_file", "blueprint/terraform.tfvars"),
            backend_config=raw.get("backend_config", {"path": ".tdgov/state/terraform.tfstate"}),
            work_dir=raw.get("work_dir", ".tdgov/work"),
            terraform_bin=raw.get("terraform_bin", "terraform"),
            conftest_bin=raw.get("conftest_bin", "conftest"),
            ledger=_build(LedgerConfig, raw.get("ledger")),
            approval=_build(ApprovalPolicy, raw.get("approval")),
            approvers=[_build(Approver, a) for a in raw.get("approvers", [])],
            notify=_build(NotifyConfig, raw.get("notify")),
            secrets=_build(SecretsConfig, raw.get("secrets")),
            proxmox=_build(ProxmoxConfig, raw.get("proxmox")),
            scan_unmanaged=raw.get("scan_unmanaged", True),
            unmanaged_vmid_range=raw.get("unmanaged_vmid_range"),
            base_dir=str(path.parent.resolve()),
        )
    except TypeError as exc:  # wrong field types / names
        raise ConfigError(str(exc)) from exc
    if cfg.approval.quorum < 1:
        raise ConfigError("approval.quorum must be >= 1")
    return cfg
