"""ContractEmitter: RUBRIC.md Phase 1 exit ("the same target produces the
same digest and evidence layout twice") plus every field this module's
brief calls out: observer coverage, run count, clean-state independence,
protocol/SDK versions, planner model/prompt digest, reproduction-count-
only confidence, evidence ref, supersedes, and identity-mismatch safety.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from babi import canon
from babi.classify import (
    BehaviorClassification,
    DiscrepancyClass,
    PropertyClaim,
    PropertyStatus,
    classify_history,
)
from babi.contract import ContractError
from babi.contract.emit import ContractEmitter
from babi.evidence import EvidenceStore
from babi.history import (
    HistoryEvent,
    InvocationStatus,
    ObservationStatus,
    ObserverResult,
    ProbeHistory,
)
from babi.plan import ProbePrimitive

TOOL_SUBJECT = "sha256:" + "ab" * 32
TOOL_DEF = {
    "name": "create_widget",
    "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}},
    "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
}


def _discovery_bundle(*, tool_def: dict = TOOL_DEF, tool_subject: str = TOOL_SUBJECT) -> dict:
    return {
        "kind": "discover/v0",
        "transport": "stdio",
        "package_or_endpoint": "@example/widgets-mcp",
        "negotiated_protocol_revision": "2026-07-28",
        "client_sdk": {"name": "mcp", "version": "2.0.0", "language": "python", "babi_version": "0.1.0"},
        "runtime_digests": {"npm_package_version": "1.2.0", "npm_package_integrity": None, "artifact_digest": None},
        "tools_list": [tool_def],
        "server_subject": "sha256:" + "11" * 32,
        "tool_subjects": {tool_def["name"]: tool_subject},
        "run": {
            "target_name": "widgets",
            "started_at": "2026-08-20T00:00:00Z",
            "finished_at": "2026-08-20T00:00:01Z",
            "tool_count": 1,
        },
    }


def _history(
    store: EvidenceStore,
    *,
    readings: list[str],
    tool_subject: str = TOOL_SUBJECT,
    tool_name: str = "create_widget",
    clean_state_id: str = "clean-1",
    finished_at: str | None = "2026-08-20T00:00:02Z",
    plan_envelope: dict | None = None,
) -> ProbeHistory:
    plan_dg = store.put_json(
        plan_envelope
        or {
            "kind": "plan/v0",
            "plan": {"subject_digest": tool_subject, "cleanup_budget": 0},
            "pass_kind": "deterministic",
            "model": None,
            "temperature": None,
            "prompt_digest": None,
            "response_digest": None,
        }
    )
    events = [
        HistoryEvent(
            sequence=i,
            step_id="step-1",
            primitive=ProbePrimitive.REPEAT,
            tool_subject=tool_subject,
            tool_name=tool_name,
            arguments={"name": "x"},
            invoked_at=f"2026-08-20T00:00:0{i}Z",
            completed_at=f"2026-08-20T00:00:0{i}.100Z",
            status=InvocationStatus.completed,
            response={"id": f"w{i}"},
            observer_results=[ObserverResult(kind="read_back", status=ObservationStatus.observed_true, value={"digest": r})],
        )
        for i, r in enumerate(readings)
    ]
    return ProbeHistory(
        run_id="run-1",
        plan_digest=plan_dg,
        target_subject=tool_subject,
        clean_state_id=clean_state_id,
        environment_digest="sha256:" + "ef" * 32,
        events=events,
        started_at="2026-08-20T00:00:00Z",
        finished_at=finished_at,
    )


def _idempotent_setup(tmp_path: Path, *, readings: list[str]) -> tuple[ContractEmitter, BehaviorClassification, dict, list[str], str]:
    store = EvidenceStore(tmp_path / "evidence")
    history = _history(store, readings=readings)
    history_dg = store.put_json(history.model_dump(mode="json"))
    classification = classify_history(history, TOOL_SUBJECT, "create_widget", tool_definition=TOOL_DEF)
    evidence_dg = store.put_json({"kind": "probe-evidence/v0", "history_digests": [history_dg]})
    emitter = ContractEmitter(store, prober_version="0.1.0-test")
    return emitter, classification, _discovery_bundle(), [history_dg], evidence_dg


# -- determinism (RUBRIC.md Phase 1 exit) --------------------------------------


def test_same_target_produces_the_same_digest_twice(tmp_path: Path) -> None:
    emitter, classification, bundle, history_digests, evidence_dg = _idempotent_setup(tmp_path, readings=["d1", "d1"])

    statement_1 = emitter.emit(classification, bundle, history_digests, evidence_dg)
    statement_2 = emitter.emit(classification, bundle, history_digests, evidence_dg)

    assert statement_1 == statement_2
    assert canon.digest(statement_1) == canon.digest(statement_2)


def test_determinism_survives_a_fresh_evidence_store_instance(tmp_path: Path) -> None:
    """Not relying on any in-process cache: re-opening the same
    content-addressed root from a brand new `EvidenceStore` must still
    dereference to the same bytes and produce the same statement."""
    store = EvidenceStore(tmp_path / "evidence")
    history = _history(store, readings=["d1", "d1"])
    history_dg = store.put_json(history.model_dump(mode="json"))
    classification = classify_history(history, TOOL_SUBJECT, "create_widget", tool_definition=TOOL_DEF)
    evidence_dg = store.put_json({"kind": "probe-evidence/v0"})
    bundle = _discovery_bundle()

    statement_a = ContractEmitter(EvidenceStore(tmp_path / "evidence")).emit(
        classification, bundle, [history_dg], evidence_dg
    )
    statement_b = ContractEmitter(EvidenceStore(tmp_path / "evidence")).emit(
        classification, bundle, [history_dg], evidence_dg
    )
    assert statement_a == statement_b


def test_dict_field_order_does_not_affect_the_digest(tmp_path: Path) -> None:
    """Guards against accidentally hashing a non-canonicalized dict
    somewhere: two structurally-identical bundles built with keys in a
    different order must still emit byte-identical statements."""
    emitter, classification, bundle, history_digests, evidence_dg = _idempotent_setup(tmp_path, readings=["d1", "d1"])
    reordered_bundle = dict(reversed(list(bundle.items())))
    statement_1 = emitter.emit(classification, bundle, history_digests, evidence_dg)
    statement_2 = emitter.emit(classification, reordered_bundle, history_digests, evidence_dg)
    assert statement_1 == statement_2


# -- safety validation ----------------------------------------------------------


def test_identity_mismatch_between_classification_and_bundle_raises(tmp_path: Path) -> None:
    emitter, classification, bundle, history_digests, evidence_dg = _idempotent_setup(tmp_path, readings=["d1", "d1"])
    tampered_bundle = dict(bundle)
    tampered_bundle["tool_subjects"] = {"create_widget": "sha256:" + "99" * 32}
    with pytest.raises(ContractError, match="mismatched identity"):
        emitter.emit(classification, tampered_bundle, history_digests, evidence_dg)


def test_empty_history_digests_raises(tmp_path: Path) -> None:
    emitter, classification, bundle, _history_digests, evidence_dg = _idempotent_setup(tmp_path, readings=["d1", "d1"])
    with pytest.raises(ContractError, match="at least one history digest"):
        emitter.emit(classification, bundle, [], evidence_dg)


def test_tool_not_in_discovery_bundle_raises(tmp_path: Path) -> None:
    emitter, classification, bundle, history_digests, evidence_dg = _idempotent_setup(tmp_path, readings=["d1", "d1"])
    bundle = dict(bundle)
    bundle["tools_list"] = []
    bundle["tool_subjects"] = {}
    classification_for_missing_tool = classification.model_copy(update={"tool_subject": "sha256:" + "77" * 32})
    with pytest.raises(ContractError):
        emitter.emit(classification_for_missing_tool, bundle, history_digests, evidence_dg)


def test_unresolvable_history_digest_raises(tmp_path: Path) -> None:
    emitter, classification, bundle, _history_digests, evidence_dg = _idempotent_setup(tmp_path, readings=["d1", "d1"])
    with pytest.raises(ContractError, match="could not load history"):
        emitter.emit(classification, bundle, ["sha256:" + "00" * 32], evidence_dg)


# -- idempotent/reversible tri-state mapping -----------------------------------


def test_idempotent_true_when_repeated_reads_agree(tmp_path: Path) -> None:
    emitter, classification, bundle, history_digests, evidence_dg = _idempotent_setup(tmp_path, readings=["d1", "d1"])
    statement = emitter.emit(classification, bundle, history_digests, evidence_dg)
    assert statement["predicate"]["observed"]["idempotent"] is True


def test_idempotent_false_when_repeated_reads_disagree(tmp_path: Path) -> None:
    emitter, classification, bundle, history_digests, evidence_dg = _idempotent_setup(tmp_path, readings=["d1", "d2"])
    statement = emitter.emit(classification, bundle, history_digests, evidence_dg)
    assert statement["predicate"]["observed"]["idempotent"] is False


def test_idempotent_unknown_when_no_read_back_evidence(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "evidence")
    plan_dg = store.put_json({"kind": "plan/v0", "plan": {}, "model": None, "prompt_digest": None})
    events = [
        HistoryEvent(
            sequence=0, step_id="step-1", primitive=ProbePrimitive.CALL_ONCE, tool_subject=TOOL_SUBJECT,
            tool_name="create_widget", arguments={}, invoked_at="2026-08-20T00:00:00Z",
            completed_at="2026-08-20T00:00:00.1Z", status=InvocationStatus.completed, response={"id": "w0"},
            observer_results=[],
        )
    ]
    history = ProbeHistory(
        run_id="run-1", plan_digest=plan_dg, target_subject=TOOL_SUBJECT, clean_state_id="clean-1",
        environment_digest="sha256:" + "ef" * 32, events=events,
        started_at="2026-08-20T00:00:00Z", finished_at="2026-08-20T00:00:01Z",
    )
    history_dg = store.put_json(history.model_dump(mode="json"))
    # CALL_ONCE contributes no idempotency claim at all (classify_idempotency requires >=2 events)
    classification = classify_history(history, TOOL_SUBJECT, "create_widget", tool_definition=TOOL_DEF)
    assert classification.claims == []

    evidence_dg = store.put_json({"kind": "probe-evidence/v0"})
    emitter = ContractEmitter(store)
    statement = emitter.emit(classification, _discovery_bundle(), [history_dg], evidence_dg)
    assert statement["predicate"]["observed"]["idempotent"] == "unknown"


def test_reversible_defaults_to_unknown_when_no_reversible_claim(tmp_path: Path) -> None:
    emitter, classification, bundle, history_digests, evidence_dg = _idempotent_setup(tmp_path, readings=["d1", "d1"])
    statement = emitter.emit(classification, bundle, history_digests, evidence_dg)
    assert statement["predicate"]["observed"]["reversible"] == "unknown"


def test_reversible_true_when_a_reversible_claim_is_present(tmp_path: Path) -> None:
    emitter, classification, bundle, history_digests, evidence_dg = _idempotent_setup(tmp_path, readings=["d1", "d1"])
    augmented = classification.model_copy(
        update={
            "claims": [
                *classification.claims,
                PropertyClaim(
                    property="reversible", status=PropertyStatus.observed_true, runs=1, scope="PERTURB_AND_UNDO"
                ),
            ]
        }
    )
    statement = emitter.emit(augmented, bundle, history_digests, evidence_dg)
    assert statement["predicate"]["observed"]["reversible"] is True


# -- confidence: derived only from reproduction count, always in [0, 1] --------


def test_confidence_is_one_when_a_single_claim_exists(tmp_path: Path) -> None:
    emitter, classification, bundle, history_digests, evidence_dg = _idempotent_setup(tmp_path, readings=["d1", "d1"])
    statement = emitter.emit(classification, bundle, history_digests, evidence_dg)
    confidence = statement["predicate"]["provenance"]["confidence"]
    assert confidence["method"] == "reproduction_count"
    assert confidence["reproduced_runs"] == confidence["total_runs"] == 2
    assert confidence["value"] == 1.0


def test_confidence_never_exceeds_one_across_unevenly_reproduced_claims(tmp_path: Path) -> None:
    """A claim's `.runs` counts readings within a history (can exceed the
    number of independent histories), so confidence must be computed from
    claim.runs alone, never divided by history/subject counts -- this is
    a regression test for exactly that unit-mismatch bug."""
    emitter, classification, bundle, history_digests, evidence_dg = _idempotent_setup(tmp_path, readings=["d1", "d1"])
    augmented = classification.model_copy(
        update={
            "claims": [
                *classification.claims,  # idempotent, runs=2
                PropertyClaim(property="reversible", status=PropertyStatus.observed_true, runs=9, scope="PERTURB_AND_UNDO"),
            ]
        }
    )
    statement = emitter.emit(augmented, bundle, history_digests, evidence_dg)
    confidence = statement["predicate"]["provenance"]["confidence"]
    assert confidence["reproduced_runs"] == 2
    assert confidence["total_runs"] == 9
    assert 0.0 <= confidence["value"] <= 1.0
    assert confidence["value"] == pytest.approx(2 / 9)


def test_confidence_zero_when_no_claims(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "evidence")
    history = _history(store, readings=["only-one"])
    history_dg = store.put_json(history.model_dump(mode="json"))
    empty_classification = BehaviorClassification(
        tool_subject=TOOL_SUBJECT, tool_name="create_widget", claims=[], discrepancy_classes=[], history_digests=[history_dg]
    )
    evidence_dg = store.put_json({"kind": "probe-evidence/v0"})
    emitter = ContractEmitter(store)
    statement = emitter.emit(empty_classification, _discovery_bundle(), [history_dg], evidence_dg)
    confidence = statement["predicate"]["provenance"]["confidence"]
    assert confidence == {"method": "reproduction_count", "reproduced_runs": 0, "total_runs": 0, "value": 0.0}


# -- side effects, discrepancy classes ------------------------------------------


def test_side_effects_populated_from_undeclared_side_effect_discrepancy(tmp_path: Path) -> None:
    emitter, classification, bundle, history_digests, evidence_dg = _idempotent_setup(tmp_path, readings=["d1", "d1"])
    augmented = classification.model_copy(update={"discrepancy_classes": [DiscrepancyClass.undeclared_side_effect]})
    statement = emitter.emit(augmented, bundle, history_digests, evidence_dg)
    assert statement["predicate"]["observed"]["side_effects"] == [
        {"kind": "undeclared_side_effect", "confidence": "observed"}
    ]
    assert statement["predicate"]["provenance"]["discrepancy_classes"] == ["undeclared_side_effect"]


def test_no_side_effects_when_no_discrepancies(tmp_path: Path) -> None:
    emitter, classification, bundle, history_digests, evidence_dg = _idempotent_setup(tmp_path, readings=["d1", "d1"])
    statement = emitter.emit(classification, bundle, history_digests, evidence_dg)
    assert statement["predicate"]["observed"]["side_effects"] == []


# -- provenance: observer coverage, run count, clean state ----------------------


def test_observer_coverage_is_sorted_and_deduped(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "evidence")
    plan_dg = store.put_json({"kind": "plan/v0", "plan": {}, "model": None, "prompt_digest": None})
    events = [
        HistoryEvent(
            sequence=0, step_id="step-1", primitive=ProbePrimitive.REPEAT, tool_subject=TOOL_SUBJECT,
            tool_name="create_widget", arguments={}, invoked_at="2026-08-20T00:00:00Z",
            completed_at="2026-08-20T00:00:00.1Z", status=InvocationStatus.completed, response={},
            observer_results=[
                ObserverResult(kind="read_back", status=ObservationStatus.observed_true, value={"digest": "d1"}),
                ObserverResult(kind="filesystem_diff", status=ObservationStatus.observed_false, value=None),
            ],
        ),
        HistoryEvent(
            sequence=1, step_id="step-1", primitive=ProbePrimitive.REPEAT, tool_subject=TOOL_SUBJECT,
            tool_name="create_widget", arguments={}, invoked_at="2026-08-20T00:00:01Z",
            completed_at="2026-08-20T00:00:01.1Z", status=InvocationStatus.completed, response={},
            observer_results=[ObserverResult(kind="read_back", status=ObservationStatus.observed_true, value={"digest": "d1"})],
        ),
    ]
    history = ProbeHistory(
        run_id="run-1", plan_digest=plan_dg, target_subject=TOOL_SUBJECT, clean_state_id="clean-1",
        environment_digest="sha256:" + "ef" * 32, events=events,
        started_at="2026-08-20T00:00:00Z", finished_at="2026-08-20T00:00:02Z",
    )
    history_dg = store.put_json(history.model_dump(mode="json"))
    classification = classify_history(history, TOOL_SUBJECT, "create_widget", tool_definition=TOOL_DEF)
    evidence_dg = store.put_json({"kind": "probe-evidence/v0"})
    statement = ContractEmitter(store).emit(classification, _discovery_bundle(), [history_dg], evidence_dg)
    assert statement["predicate"]["provenance"]["observer_coverage"] == ["filesystem_diff", "read_back"]


def test_clean_state_tracks_distinct_ids_across_histories(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "evidence")
    history_a = _history(store, readings=["d1", "d1"], clean_state_id="clean-a")
    history_b = _history(store, readings=["d1", "d1"], clean_state_id="clean-b")
    dg_a = store.put_json(history_a.model_dump(mode="json"))
    dg_b = store.put_json(history_b.model_dump(mode="json"))
    classification_a = classify_history(history_a, TOOL_SUBJECT, "create_widget", tool_definition=TOOL_DEF)
    classification_b = classify_history(history_b, TOOL_SUBJECT, "create_widget", tool_definition=TOOL_DEF)
    from babi.classify import aggregate_reproductions

    merged = aggregate_reproductions([classification_a, classification_b])
    evidence_dg = store.put_json({"kind": "probe-evidence/v0"})
    statement = ContractEmitter(store).emit(merged, _discovery_bundle(), [dg_a, dg_b], evidence_dg)

    clean_state = statement["predicate"]["provenance"]["clean_state"]
    assert clean_state["run_count"] == 2
    assert clean_state["distinct_clean_state_ids"] == 2
    assert clean_state["clean_state_ids"] == ["clean-a", "clean-b"]


def test_verified_at_is_the_max_finished_at_across_histories(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "evidence")
    history_a = _history(store, readings=["d1", "d1"], clean_state_id="clean-a", finished_at="2026-08-20T00:00:02Z")
    history_b = _history(store, readings=["d1", "d1"], clean_state_id="clean-b", finished_at="2026-08-25T00:00:02Z")
    dg_a = store.put_json(history_a.model_dump(mode="json"))
    dg_b = store.put_json(history_b.model_dump(mode="json"))
    classification = classify_history(history_a, TOOL_SUBJECT, "create_widget", tool_definition=TOOL_DEF)
    evidence_dg = store.put_json({"kind": "probe-evidence/v0"})
    statement = ContractEmitter(store).emit(classification, _discovery_bundle(), [dg_a, dg_b], evidence_dg)
    assert statement["predicate"]["verified_at"] == "2026-08-25T00:00:02Z"


# -- subject digest, tool version, evidence ref, supersedes ---------------------


def test_subject_digest_is_bare_hex_no_prefix(tmp_path: Path) -> None:
    emitter, classification, bundle, history_digests, evidence_dg = _idempotent_setup(tmp_path, readings=["d1", "d1"])
    statement = emitter.emit(classification, bundle, history_digests, evidence_dg)
    digest_field = statement["subject"][0]["digest"]["sha256"]
    assert digest_field == "ab" * 32
    assert ":" not in digest_field


def test_tool_version_sourced_from_npm_package_version(tmp_path: Path) -> None:
    emitter, classification, bundle, history_digests, evidence_dg = _idempotent_setup(tmp_path, readings=["d1", "d1"])
    statement = emitter.emit(classification, bundle, history_digests, evidence_dg)
    assert statement["subject"][0]["digest"]["toolVersion"] == "1.2.0"


def test_evidence_ref_matches_the_given_evidence_digest(tmp_path: Path) -> None:
    emitter, classification, bundle, history_digests, evidence_dg = _idempotent_setup(tmp_path, readings=["d1", "d1"])
    statement = emitter.emit(classification, bundle, history_digests, evidence_dg)
    evidence_ref = statement["predicate"]["evidence_ref"]
    assert evidence_ref["sha256"] == evidence_dg
    assert evidence_ref["uri"] == f"cas://sha256/{evidence_dg.removeprefix('sha256:')}"


def test_supersedes_passed_through_when_given(tmp_path: Path) -> None:
    emitter, classification, bundle, history_digests, evidence_dg = _idempotent_setup(tmp_path, readings=["d1", "d1"])
    prior_digest = "sha256:" + "22" * 32
    statement = emitter.emit(classification, bundle, history_digests, evidence_dg, supersedes=prior_digest)
    assert statement["predicate"]["supersedes"] == prior_digest


def test_supersedes_omitted_when_none(tmp_path: Path) -> None:
    emitter, classification, bundle, history_digests, evidence_dg = _idempotent_setup(tmp_path, readings=["d1", "d1"])
    statement = emitter.emit(classification, bundle, history_digests, evidence_dg)
    assert "supersedes" not in statement["predicate"]


# -- planner provenance ----------------------------------------------------------


def test_planner_model_and_prompt_digest_from_plan_envelope(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "evidence")
    prompt_dg = store.put_json({"kind": "probe-prompt/v0", "text": "..."})
    history = _history(
        store,
        readings=["d1", "d1"],
        plan_envelope={
            "kind": "plan/v0",
            "plan": {"subject_digest": TOOL_SUBJECT, "cleanup_budget": 0},
            "pass_kind": "model",
            "model": "claude-test-planner",
            "temperature": 0.0,
            "prompt_digest": prompt_dg,
            "response_digest": None,
        },
    )
    history_dg = store.put_json(history.model_dump(mode="json"))
    classification = classify_history(history, TOOL_SUBJECT, "create_widget", tool_definition=TOOL_DEF)
    evidence_dg = store.put_json({"kind": "probe-evidence/v0"})
    statement = ContractEmitter(store).emit(classification, _discovery_bundle(), [history_dg], evidence_dg)
    planner = statement["predicate"]["provenance"]["planner"]
    assert planner == {"model": "claude-test-planner", "prompt_digest": prompt_dg}


def test_planner_fields_absent_when_plan_envelope_has_none(tmp_path: Path) -> None:
    emitter, classification, bundle, history_digests, evidence_dg = _idempotent_setup(tmp_path, readings=["d1", "d1"])
    statement = emitter.emit(classification, bundle, history_digests, evidence_dg)
    assert statement["predicate"]["provenance"]["planner"] == {}


# -- operation / vendor_declared -------------------------------------------------


def test_operation_carries_vendor_declared_annotations_verbatim(tmp_path: Path) -> None:
    emitter, classification, bundle, history_digests, evidence_dg = _idempotent_setup(tmp_path, readings=["d1", "d1"])
    statement = emitter.emit(classification, bundle, history_digests, evidence_dg)
    assert statement["predicate"]["operation"]["vendor_declared"] == TOOL_DEF["annotations"]
    assert statement["predicate"]["operation"]["protocol"] == "mcp"
    assert statement["predicate"]["operation"]["name"] == "create_widget"


def test_input_schema_digest_is_canonical_digest_of_input_schema(tmp_path: Path) -> None:
    emitter, classification, bundle, history_digests, evidence_dg = _idempotent_setup(tmp_path, readings=["d1", "d1"])
    statement = emitter.emit(classification, bundle, history_digests, evidence_dg)
    assert statement["predicate"]["operation"]["input_schema_digest"] == canon.digest(TOOL_DEF["inputSchema"])
