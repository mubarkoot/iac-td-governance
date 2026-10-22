"""Change requests, multi-stakeholder approval and approved baselines.

Implements Steps 3 and 6 of the framework (DR2, DR3):

* A blueprint change is submitted as a *change request* (CR) that references
  the canonical bundle by digest. The bundle itself is stored in the ledger.
* Approvers registered in the configuration record approvals in the ledger.
  A CR becomes the new *approved baseline* once the approval policy is met
  (quorum of distinct approvers, required roles, no self-approval).
* The approved-baseline pointer is a single ledger key; its history is the
  verifiable record of every baseline the organization has approved.
* A break-glass record allows a time-limited, attributed out-of-band change
  for urgent incidents; audits classify drift during an active break-glass
  window as expected but still report it.

Identities are taken from the configuration. Production deployments should
bind them to the organization's identity provider (e.g. SSO or signed Git
commits); see docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from .canonical import Bundle, digest_of
from .ledger import Ledger


class GovernanceError(Exception):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ts_key() -> str:
    return f"{time.time_ns():020d}"


class Governance:
    def __init__(self, ledger: Ledger, cfg):
        self.ledger = ledger
        self.cfg = cfg
        self.ns = f"bp:{cfg.blueprint_name}"

    # ------------------------------------------------------------------ keys
    def _k(self, *parts: str) -> str:
        return ":".join([self.ns, *parts])

    # ----------------------------------------------------------------- blobs
    def store_bundle(self, bundle: Bundle) -> str:
        key = self._k("blob", bundle.digest)
        if self.ledger.get(key) is None:
            self.ledger.put(key, bundle.data)
            self.ledger.put_json(self._k("manifest", bundle.digest), bundle.manifest)
        return key

    def load_bundle(self, digest: str) -> bytes:
        e = self.ledger.get(self._k("blob", digest))
        if e is None:
            raise GovernanceError(f"bundle {digest[:12]} not found in ledger")
        if digest_of(e.value) != digest:
            raise GovernanceError(f"bundle {digest[:12]} does not match its digest")
        return e.value

    def manifest(self, digest: str) -> dict:
        return self.ledger.get_json(self._k("manifest", digest)) or {}

    # ------------------------------------------------------- change requests
    def submit(self, bundle: Bundle, author: str, message: str, validation: dict | None = None) -> dict:
        self.store_bundle(bundle)
        base = self.approved_baseline()
        if base and base["digest"] == bundle.digest:
            raise GovernanceError("blueprint is identical to the current approved baseline")
        cr_id = f"cr-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{bundle.digest[:8]}"
        cr = {
            "cr_id": cr_id,
            "digest": bundle.digest,
            "author": author,
            "message": message,
            "created_at": now_iso(),
            "status": "open",
            "base_digest": base["digest"] if base else None,
            "validation": validation or {},
            "size_bytes": bundle.size,
        }
        self.ledger.put_json(self._k("cr", cr_id), cr)
        return cr

    def get_cr(self, cr_id: str) -> dict:
        cr = self.ledger.get_json(self._k("cr", cr_id))
        if cr is None:
            raise GovernanceError(f"change request {cr_id} not found")
        return cr

    def list_crs(self, status: str | None = None) -> list[dict]:
        import json

        crs = [json.loads(e.value) for e in self.ledger.scan(self._k("cr", ""))]
        return [c for c in crs if status is None or c["status"] == status]

    def approvals(self, cr_id: str) -> list[dict]:
        import json

        return [json.loads(e.value) for e in self.ledger.scan(self._k("approval", cr_id, ""))]

    def approve(self, cr_id: str, approver_id: str, comment: str = "") -> dict:
        cr = self.get_cr(cr_id)
        if cr["status"] != "open":
            raise GovernanceError(f"{cr_id} is {cr['status']}, not open")
        approver = self.cfg.approver(approver_id)
        if approver is None:
            raise GovernanceError(f"'{approver_id}' is not a registered approver")
        if approver_id == cr["author"] and not self.cfg.approval.allow_self_approval:
            raise GovernanceError("authors cannot approve their own change requests")
        base = self.approved_baseline()
        if (base["digest"] if base else None) != cr["base_digest"]:
            raise GovernanceError("the approved baseline changed since this CR was submitted; resubmit it")
        record = {
            "cr_id": cr_id,
            "approver": approver_id,
            "role": approver.role,
            "decision": "approve",
            "comment": comment,
            "at": now_iso(),
            "digest": cr["digest"],
        }
        self.ledger.put_json(self._k("approval", cr_id, approver_id), record)
        decision = self.evaluate(cr_id)
        if decision["satisfied"]:
            self._promote(cr, decision)
        return {"approval": record, "policy": decision}

    def reject(self, cr_id: str, approver_id: str, reason: str) -> dict:
        cr = self.get_cr(cr_id)
        if self.cfg.approver(approver_id) is None:
            raise GovernanceError(f"'{approver_id}' is not a registered approver")
        if cr["status"] != "open":
            raise GovernanceError(f"{cr_id} is {cr['status']}, not open")
        cr = {**cr, "status": "rejected", "closed_at": now_iso(), "closed_by": approver_id, "reason": reason}
        self.ledger.put_json(self._k("cr", cr_id), cr)
        return cr

    def evaluate(self, cr_id: str) -> dict:
        cr = self.get_cr(cr_id)
        policy = self.cfg.approval
        valid = [a for a in self.approvals(cr_id)
                 if a["decision"] == "approve" and a["digest"] == cr["digest"]
                 and (policy.allow_self_approval or a["approver"] != cr["author"])]
        approvers = sorted({a["approver"] for a in valid})
        roles = {a["role"] for a in valid}
        missing_roles = sorted(set(policy.required_roles) - roles)
        return {
            "approvers": approvers,
            "quorum": policy.quorum,
            "missing_roles": missing_roles,
            "satisfied": len(approvers) >= policy.quorum and not missing_roles,
        }

    def _promote(self, cr: dict, decision: dict) -> dict:
        previous = self.approved_baseline()
        baseline = {
            "version": (previous["version"] + 1) if previous else 1,
            "digest": cr["digest"],
            "cr_id": cr["cr_id"],
            "author": cr["author"],
            "approvers": decision["approvers"],
            "approved_at": now_iso(),
            "previous_digest": previous["digest"] if previous else None,
        }
        self.ledger.put_json(self._k("approved"), baseline)
        self.ledger.put_json(self._k("cr", cr["cr_id"]), {**cr, "status": "approved", "closed_at": baseline["approved_at"],
                                                           "baseline_version": baseline["version"]})
        self.record_event("baseline-approved", baseline)
        return baseline

    # -------------------------------------------------------------- baseline
    def approved_baseline(self) -> dict | None:
        return self.ledger.get_json(self._k("approved"))

    def baseline_history(self) -> list[dict]:
        import json

        return [json.loads(e.value) for e in self.ledger.history(self._k("approved"))]

    # ---------------------------------------------------------------- events
    def record_event(self, kind: str, payload: dict) -> str:
        key = self._k("event", ts_key(), kind)
        self.ledger.put_json(key, {"kind": kind, "at": now_iso(), **payload})
        return key

    def events(self, kind: str | None = None, since: datetime | None = None) -> list[dict]:
        import json

        out = []
        for e in self.ledger.scan(self._k("event", "")):
            ev = json.loads(e.value)
            if kind and ev.get("kind") != kind:
                continue
            if since and datetime.fromisoformat(ev["at"]) < since:
                continue
            out.append(ev)
        return out

    # ----------------------------------------------------------- break-glass
    def breakglass_open(self, actor: str, reason: str, ttl_minutes: int = 60) -> dict:
        if not reason.strip():
            raise GovernanceError("a reason is required for break-glass access")
        if ttl_minutes <= 0 or ttl_minutes > 24 * 60:
            raise GovernanceError("break-glass TTL must be between 1 minute and 24 hours")
        opened = datetime.now(timezone.utc)
        record = {
            "state": "open",
            "actor": actor,
            "reason": reason,
            "opened_at": opened.isoformat(timespec="seconds"),
            "expires_at": (opened + timedelta(minutes=ttl_minutes)).isoformat(timespec="seconds"),
        }
        self.ledger.put_json(self._k("breakglass"), record)
        self.record_event("breakglass-opened", record)
        return record

    def breakglass_close(self, actor: str, resolution: str) -> dict:
        current = self.ledger.get_json(self._k("breakglass"))
        if not current or current["state"] != "open":
            raise GovernanceError("no open break-glass window")
        record = {**current, "state": "closed", "closed_by": actor, "closed_at": now_iso(), "resolution": resolution}
        self.ledger.put_json(self._k("breakglass"), record)
        self.record_event("breakglass-closed", record)
        return record

    def breakglass_active(self, at: datetime | None = None) -> dict | None:
        current = self.ledger.get_json(self._k("breakglass"))
        if not current or current["state"] != "open":
            return None
        at = at or datetime.now(timezone.utc)
        return current if datetime.fromisoformat(current["expires_at"]) > at else None
