"""Deployment-time secret injection (design rule 2 of the paper).

Secrets are resolved from the configured provider and passed to Terraform as
``TF_VAR_<name>`` environment variables of the child process only. They are
never written to the blueprint, the ledger, or log output.
"""

from __future__ import annotations

import os

import requests


class SecretError(Exception):
    pass


def resolve(cfg) -> dict[str, str]:
    sc = cfg.secrets
    values: dict[str, str] = {}
    for var, ref in sc.mapping.items():
        if sc.provider == "env":
            val = os.environ.get(ref)
            if val is None:
                raise SecretError(f"environment variable {ref} (for {var}) is not set")
        elif sc.provider == "vault":
            val = _vault_kv2(ref, os.environ.get(sc.vault_addr_env), os.environ.get(sc.vault_token_env))
        else:
            raise SecretError(f"unknown secrets provider: {sc.provider}")
        values[var] = val
    return values


def as_tf_env(secrets: dict[str, str]) -> dict[str, str]:
    return {f"TF_VAR_{k}": v for k, v in secrets.items()}


def _vault_kv2(ref: str, addr: str | None, token: str | None) -> str:
    if not addr or not token:
        raise SecretError("VAULT_ADDR and VAULT_TOKEN must be set for the vault secrets provider")
    if "#" not in ref:
        raise SecretError(f"vault reference must be '<path>#<field>': {ref}")
    path, field = ref.split("#", 1)
    resp = requests.get(f"{addr.rstrip('/')}/v1/{path.lstrip('/')}", headers={"X-Vault-Token": token}, timeout=10)
    if resp.status_code != 200:
        raise SecretError(f"vault returned HTTP {resp.status_code} for {path}")
    data = resp.json().get("data", {}).get("data", {})
    if field not in data:
        raise SecretError(f"field '{field}' not found at {path}")
    return str(data[field])


def redact(text: str, secrets: dict[str, str]) -> str:
    for v in secrets.values():
        if v:
            text = text.replace(v, "***")
    return text
