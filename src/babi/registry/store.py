"""Registry: SQLite index (db.py's schema) over content-addressed contract
and evidence bodies (`babi.evidence.EvidenceStore`).

SQLite is a queryable projection, never the source of truth -- every
contract this registry knows about also lives, byte-identical, in the CAS
under its own digest. `resolve()` computes exact subject digests by
reusing `babi.subject.server_subject`/`tool_subject` (never reimplemented
here); every other lookup is by exact digest, never a tool/operation name
(RUBRIC.md's "No fuzzy names" carries through to this whole module).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from babi.attest.cosign import rekor_log_index_from_bundle
from babi.canon import Json
from babi.canon import digest as canon_digest
from babi.evidence import EvidenceStore
from babi.registry.db import connect
from babi.registry.policy import DEFAULT_INTENT, DEFAULT_MAX_AGE, PolicyResult, decide
from babi.subject import server_subject, tool_subject


class RegistryError(ValueError):
    """A registry operation was given malformed input: not a sha256
    digest, not a well-formed contract record, or similar."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bare_hex(value: str) -> str:
    _, sep, hexpart = value.partition(":")
    candidate = hexpart if sep else value
    if len(candidate) != 64 or any(ch not in "0123456789abcdef" for ch in candidate.lower()):
        raise RegistryError(f"not a sha256 hex digest: {value!r}")
    return candidate.lower()


def _prefixed(value: str) -> str:
    """Normalize any accepted digest spelling to babi's internal
    `"sha256:<hex>"` form, which is what every registry table stores and
    every registry method accepts/returns."""
    return f"sha256:{_bare_hex(value)}"


@dataclass(frozen=True, slots=True)
class Observation:
    contract_digest: str
    subject_digest: str
    observed_at: str
    predicate_type: str
    evidence_digest: str
    rekor_log_index: int | None
    supersedes_digest: str | None

    def to_json(self) -> dict[str, Json]:
        return {
            "contract_digest": self.contract_digest,
            "subject_digest": self.subject_digest,
            "observed_at": self.observed_at,
            "predicate_type": self.predicate_type,
            "evidence_digest": self.evidence_digest,
            "rekor_log_index": self.rekor_log_index,
            "supersedes_digest": self.supersedes_digest,
        }


@dataclass(frozen=True, slots=True)
class ClaimRow:
    contract_digest: str
    property: str
    status: str
    value: Json
    coverage: Json


def _row_to_observation(row: sqlite3.Row) -> Observation:
    return Observation(
        contract_digest=row["contract_digest"],
        subject_digest=row["subject_digest"],
        observed_at=row["observed_at"],
        predicate_type=row["predicate_type"],
        evidence_digest=row["evidence_digest"],
        rekor_log_index=row["rekor_log_index"],
        supersedes_digest=row["supersedes_digest"],
    )


class Registry:
    def __init__(self, db_path: str | Path, evidence: EvidenceStore) -> None:
        self._conn = connect(db_path)
        self._evidence = evidence
        # `Registry` is shared across a `ThreadingHTTPServer`'s one-thread-per-request
        # workers; the connection itself is opened `check_same_thread=False` (db.py) and
        # every access here is serialized through this reentrant lock (reentrant because
        # `policy()`/`insert_contract()` call other locked methods on `self`).
        self._lock = threading.RLock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> Registry:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- identity ---------------------------------------------------------

    def resolve(
        self,
        *,
        registry_name: str | None,
        registry_version: str | None,
        package_or_endpoint: str,
        runtime_artifact_digest: str | None,
        protocol_revision: str,
        tool_definition: dict[str, Json],
    ) -> str:
        """Exact tool_subject digest for `tool_definition`, computed (never
        guessed) via `babi.subject`. Records/refreshes the subject's
        `subjects` row; never mutates `registry_name`/`registry_version`
        from non-null to null on an already-known subject."""
        srv = server_subject(
            registry_name=registry_name,
            registry_version=registry_version,
            package_or_endpoint=package_or_endpoint,
            runtime_artifact_digest=runtime_artifact_digest,
            protocol_revision=protocol_revision,
        )
        digest = tool_subject(srv, tool_definition)
        now = _now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO subjects
                    (digest, registry_name, registry_version, package_or_endpoint, protocol_revision, first_seen_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(digest) DO UPDATE SET
                    registry_name = COALESCE(subjects.registry_name, excluded.registry_name),
                    registry_version = COALESCE(subjects.registry_version, excluded.registry_version),
                    package_or_endpoint = excluded.package_or_endpoint,
                    protocol_revision = excluded.protocol_revision,
                    last_seen_at = excluded.last_seen_at
                """,
                (digest, registry_name, registry_version, package_or_endpoint, protocol_revision, now, now),
            )
            self._conn.commit()
        return digest

    # -- write --------------------------------------------------------------

    def insert_contract(self, record: dict[str, Json]) -> str:
        """Insert one attested contract record --
        ``{"statement": <Statement>, "dsse_envelope": {...}, "cosign_bundle": {...} | None}``
        -- append-only. Re-inserting byte-identical content (same
        `contract_digest`) is a no-op, matching `EvidenceStore`'s own
        write-once CAS semantics; supersession is *never* an in-place
        update, only a new row whose own `supersedes_digest` points back.
        """
        statement = record.get("statement")
        if not isinstance(statement, dict):
            raise RegistryError("record['statement'] missing or not an object")
        predicate = statement.get("predicate")
        subjects = statement.get("subject")
        if not isinstance(predicate, dict) or not isinstance(subjects, list) or not subjects:
            raise RegistryError("record['statement'] is not a well-formed in-toto Statement")
        first_subject = subjects[0]
        if not isinstance(first_subject, dict):
            raise RegistryError("record['statement']['subject'][0] is not an object")
        raw_sha256 = (first_subject.get("digest") or {}).get("sha256")
        predicate_type = statement.get("predicateType")
        evidence_ref = predicate.get("evidence_ref") or {}
        evidence_digest = evidence_ref.get("sha256")
        verified_at = predicate.get("verified_at")
        supersedes = predicate.get("supersedes")
        provenance = predicate.get("provenance") or {}

        if not isinstance(raw_sha256, str):
            raise RegistryError("record['statement']['subject'][0]['digest']['sha256'] missing")
        if not isinstance(predicate_type, str):
            raise RegistryError("record['statement']['predicateType'] missing")
        if not isinstance(evidence_digest, str):
            raise RegistryError("record['statement']['predicate']['evidence_ref']['sha256'] missing")
        if not isinstance(verified_at, str):
            raise RegistryError("record['statement']['predicate']['verified_at'] missing")

        subject_digest = _prefixed(raw_sha256)
        contract_digest = canon_digest(record)
        rekor_log_index = rekor_log_index_from_bundle(record.get("cosign_bundle"))
        now = _now()

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO subjects (digest, registry_name, registry_version, package_or_endpoint, protocol_revision, first_seen_at, last_seen_at)
                VALUES (?, NULL, NULL, ?, ?, ?, ?)
                ON CONFLICT(digest) DO UPDATE SET
                    package_or_endpoint = COALESCE(NULLIF(excluded.package_or_endpoint, ''), subjects.package_or_endpoint),
                    protocol_revision = COALESCE(NULLIF(excluded.protocol_revision, ''), subjects.protocol_revision),
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    subject_digest,
                    str(provenance.get("package_or_endpoint") or ""),
                    str(provenance.get("protocol_revision") or ""),
                    now,
                    now,
                ),
            )

            cursor = self._conn.execute(
                """
                INSERT OR IGNORE INTO observations
                    (contract_digest, subject_digest, observed_at, predicate_type, evidence_digest, rekor_log_index, supersedes_digest)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (contract_digest, subject_digest, verified_at, predicate_type, evidence_digest, rekor_log_index, supersedes),
            )
            newly_inserted = cursor.rowcount == 1

            if newly_inserted:
                self._evidence.put_json(record)
                for claim in provenance.get("claims") or []:
                    if not isinstance(claim, dict):
                        continue
                    self._conn.execute(
                        "INSERT INTO claims (contract_digest, property, status, value_json, coverage_json) VALUES (?, ?, ?, ?, ?)",
                        (
                            contract_digest,
                            str(claim.get("property")),
                            str(claim.get("status")),
                            json.dumps({"runs": claim.get("runs"), "detail": claim.get("detail")}),
                            json.dumps({"scope": claim.get("scope"), "evidence_digests": claim.get("evidence_digests") or []}),
                        ),
                    )
                self.set_alias(str(first_subject.get("name") or subject_digest), subject_digest)

            self._conn.commit()
        return contract_digest

    # -- read -----------------------------------------------------------------

    def latest(self, subject_digest: str) -> Observation | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT contract_digest, subject_digest, observed_at, predicate_type, evidence_digest, rekor_log_index, supersedes_digest
                FROM observations
                WHERE subject_digest = ?
                ORDER BY observed_at DESC, contract_digest DESC
                LIMIT 1
                """,
                (_prefixed(subject_digest),),
            ).fetchone()
        return _row_to_observation(row) if row is not None else None

    def history(self, subject_digest: str) -> list[Observation]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT contract_digest, subject_digest, observed_at, predicate_type, evidence_digest, rekor_log_index, supersedes_digest
                FROM observations
                WHERE subject_digest = ?
                ORDER BY observed_at ASC, contract_digest ASC
                """,
                (_prefixed(subject_digest),),
            ).fetchall()
        return [_row_to_observation(row) for row in rows]

    def claims_for(self, contract_digest: str) -> list[ClaimRow]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT contract_digest, property, status, value_json, coverage_json FROM claims WHERE contract_digest = ? ORDER BY property",
                (contract_digest,),
            ).fetchall()
        return [
            ClaimRow(
                contract_digest=row["contract_digest"],
                property=row["property"],
                status=row["status"],
                value=json.loads(row["value_json"]) if row["value_json"] is not None else None,
                coverage=json.loads(row["coverage_json"]) if row["coverage_json"] is not None else None,
            )
            for row in rows
        ]

    def get_contract(self, contract_digest: str) -> dict[str, Json]:
        raw = self._evidence.get(contract_digest)
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise RegistryError(f"contract {contract_digest!r} is not a JSON object")
        return parsed

    def get_evidence(self, evidence_digest: str) -> bytes:
        return self._evidence.get(evidence_digest)

    # -- policy -----------------------------------------------------------------

    def policy(
        self,
        subject_digest: str,
        *,
        intent: str = DEFAULT_INTENT,
        now: datetime | None = None,
        max_age: timedelta = DEFAULT_MAX_AGE,
    ) -> PolicyResult:
        """Exact-digest policy decision for `subject_digest` -- gathers the
        latest observation, its contract body, and its claims, then hands
        everything to `babi.registry.policy.decide` (a pure function).
        Never resolves an alias or a tool name; `subject_digest` must
        already be the exact digest."""
        normalized = _prefixed(subject_digest)
        observation = self.latest(normalized)
        if observation is None:
            return decide(
                intent=intent,
                subject_digest=normalized,
                observation_subject_digest=None,
                contract_digest=None,
                verified_at=None,
                claim_status=None,
                vendor_declared={},
                observer_coverage=[],
                now=now,
                max_age=max_age,
            )

        observation_subject_digest: str | None = None
        vendor_declared: dict[str, Json] = {}
        observer_coverage: list[str] = []
        try:
            contract = self.get_contract(observation.contract_digest)
            statement = contract.get("statement", {})
            predicate = statement.get("predicate", {}) if isinstance(statement, dict) else {}
            subjects = statement.get("subject", []) if isinstance(statement, dict) else []
            if subjects and isinstance(subjects[0], dict):
                raw_sha = (subjects[0].get("digest") or {}).get("sha256")
                if isinstance(raw_sha, str):
                    observation_subject_digest = _prefixed(raw_sha)
            if isinstance(predicate, dict):
                operation = predicate.get("operation") or {}
                if isinstance(operation, dict) and isinstance(operation.get("vendor_declared"), dict):
                    vendor_declared = operation["vendor_declared"]
                provenance = predicate.get("provenance") or {}
                if isinstance(provenance, dict) and isinstance(provenance.get("observer_coverage"), list):
                    observer_coverage = [k for k in provenance["observer_coverage"] if isinstance(k, str)]
        except (OSError, ValueError, RegistryError):
            # Contract body missing/corrupt from the CAS side of an otherwise-indexed
            # observation: treat exactly like an identity mismatch -- we cannot vouch
            # for a contract we can't actually read back and verify.
            observation_subject_digest = None

        claim_status: str | None = None
        for claim in self.claims_for(observation.contract_digest):
            if claim.property == "idempotent":
                claim_status = claim.status
                break

        return decide(
            intent=intent,
            subject_digest=normalized,
            observation_subject_digest=observation_subject_digest,
            contract_digest=observation.contract_digest,
            verified_at=observation.observed_at,
            claim_status=claim_status,
            vendor_declared=vendor_declared,
            observer_coverage=observer_coverage,
            now=now,
            max_age=max_age,
        )

    # -- aliases (convenience lookups only -- never consulted by policy()) ----

    def set_alias(self, alias: str, subject_digest: str) -> None:
        now = _now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO aliases (alias, subject_digest, valid_from, valid_until) VALUES (?, ?, ?, NULL)
                ON CONFLICT(alias) DO UPDATE SET subject_digest = excluded.subject_digest, valid_from = excluded.valid_from, valid_until = NULL
                """,
                (alias, _prefixed(subject_digest), now),
            )
            self._conn.commit()

    def resolve_alias(self, alias: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT subject_digest FROM aliases WHERE alias = ? AND valid_until IS NULL",
                (alias,),
            ).fetchone()
        return row["subject_digest"] if row is not None else None

    def retire_alias(self, alias: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE aliases SET valid_until = ? WHERE alias = ? AND valid_until IS NULL", (_now(), alias)
            )
            self._conn.commit()


__all__ = ["ClaimRow", "Observation", "Registry", "RegistryError"]
