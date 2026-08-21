"""Executor acceptance tests against the in-process stateful fake MCP server
(`conftest.build_stateful_server`).

These are the tests the assignment's acceptance bar names directly: a
duplicate-creation handler must classify as `observed_false` idempotency, a
deduping handler as `observed_true`, an absent effect-observer as
`inconclusive`, ancillary (non-effect) evidence must never flip the
verdict, and an interrupt attempt that never actually takes hold must
likewise stay `inconclusive` rather than being inferred either way.
"""

from __future__ import annotations

import json

import pytest
from mcp.client import Client

from babi.classify import PropertyStatus, classify_idempotency
from babi.evidence import EvidenceStore
from babi.execute import execute_plan
from babi.execute.wire import dump_model
from babi.history import InvocationStatus, ObservationStatus, history_digest
from babi.observe import observe_count_digest
from babi.plan import DependencyEdge, ProbePlan, ProbePrimitive, ProbeStep, RationaleEntry, RationaleProvenance

from conftest import CLIENT_INFO, build_stateful_server, discovery_bundle_for


def _plan(subject_digest: str, steps: list[ProbeStep]) -> ProbePlan:
    rationale = [
        RationaleEntry(
            edge=DependencyEdge(from_step=dep, to_step=step.id),
            provenance=RationaleProvenance.SCHEMA_OVERLAP,
            detail="test fixture: read-back verifies its declared dependency",
        )
        for step in steps
        for dep in step.depends_on
    ]
    return ProbePlan(subject_digest=subject_digest, steps=steps, cleanup_budget=0, rationale_evidence=rationale)


def _events_for(history, step_id: str) -> list:
    return [event for event in history.events if event.step_id == step_id]


async def test_duplicate_creation_yields_observed_false_idempotency(tmp_path) -> None:
    server, state = build_stateful_server()
    async with Client(server=server, mode="legacy", client_info=CLIENT_INFO) as client:
        bundle = await discovery_bundle_for(client)
        subjects = bundle["tool_subjects"]
        evidence = EvidenceStore(root=tmp_path)

        repeat_step = ProbeStep(
            id="create",
            primitive=ProbePrimitive.REPEAT,
            tool_subject=subjects["create_item"],
            parameters={"arguments": {"name": "a"}, "count": 2},
        )
        read_back_step = ProbeStep(
            id="verify", primitive=ProbePrimitive.READ_BACK, tool_subject=subjects["list_items"], depends_on=["create"]
        )
        plan = _plan(bundle["server_subject"], [repeat_step, read_back_step])

        result = await execute_plan(plan, client=client, discovery_bundle=bundle, evidence=evidence)

        assert len(state["items"]) == 2  # the handler really did create two distinct resources
        claims = classify_idempotency(result.history, subjects["create_item"])
        assert len(claims) == 1
        assert claims[0].property == "idempotent"
        assert claims[0].status is PropertyStatus.observed_false
        assert claims[0].scope == "REPEAT"


async def test_deduping_handler_yields_observed_true_idempotency(tmp_path) -> None:
    server, state = build_stateful_server()
    async with Client(server=server, mode="legacy", client_info=CLIENT_INFO) as client:
        bundle = await discovery_bundle_for(client)
        subjects = bundle["tool_subjects"]
        evidence = EvidenceStore(root=tmp_path)

        repeat_step = ProbeStep(
            id="create",
            primitive=ProbePrimitive.REPEAT,
            tool_subject=subjects["create_item_dedup"],
            parameters={"arguments": {"name": "a"}, "count": 2},
        )
        read_back_step = ProbeStep(
            id="verify", primitive=ProbePrimitive.READ_BACK, tool_subject=subjects["list_items"], depends_on=["create"]
        )
        plan = _plan(bundle["server_subject"], [repeat_step, read_back_step])

        result = await execute_plan(plan, client=client, discovery_bundle=bundle, evidence=evidence)

        assert len(state["items"]) == 1  # the handler deduped -- only one resource ever existed
        claims = classify_idempotency(result.history, subjects["create_item_dedup"])
        assert len(claims) == 1
        assert claims[0].status is PropertyStatus.observed_true
        assert claims[0].scope == "REPEAT"


async def test_no_read_back_step_yields_inconclusive(tmp_path) -> None:
    server, _state = build_stateful_server()
    async with Client(server=server, mode="legacy", client_info=CLIENT_INFO) as client:
        bundle = await discovery_bundle_for(client)
        subjects = bundle["tool_subjects"]
        evidence = EvidenceStore(root=tmp_path)

        repeat_step = ProbeStep(
            id="create",
            primitive=ProbePrimitive.REPEAT,
            tool_subject=subjects["create_item"],
            parameters={"arguments": {"name": "a"}, "count": 2},
        )
        plan = _plan(bundle["server_subject"], [repeat_step])  # deliberately: no READ_BACK step at all

        result = await execute_plan(plan, client=client, discovery_bundle=bundle, evidence=evidence)

        assert not any(
            observer.kind == "read_back" for event in result.history.events for observer in event.observer_results
        )
        claims = classify_idempotency(result.history, subjects["create_item"])
        assert len(claims) == 1
        assert claims[0].status is PropertyStatus.inconclusive
        assert "read_back" in claims[0].detail


async def test_ancillary_log_growth_does_not_disprove_idempotency(tmp_path) -> None:
    server, state = build_stateful_server()
    async with Client(server=server, mode="legacy", client_info=CLIENT_INFO) as client:
        bundle = await discovery_bundle_for(client)
        subjects = bundle["tool_subjects"]
        evidence = EvidenceStore(root=tmp_path)

        repeat_step = ProbeStep(
            id="create",
            primitive=ProbePrimitive.REPEAT,
            tool_subject=subjects["create_item_dedup"],
            parameters={"arguments": {"name": "a"}, "count": 2},
        )
        read_back_step = ProbeStep(
            id="verify", primitive=ProbePrimitive.READ_BACK, tool_subject=subjects["list_items"], depends_on=["create"]
        )
        plan = _plan(bundle["server_subject"], [repeat_step, read_back_step])
        result = await execute_plan(plan, client=client, discovery_bundle=bundle, evidence=evidence)

        baseline = classify_idempotency(result.history, subjects["create_item_dedup"])
        assert baseline[0].status is PropertyStatus.observed_true

        # Inject ancillary evidence directly: a `count_digest` reading of the
        # unrelated `logs` resource, taken once per repeat call, showing real
        # growth (the handler logs every call, deduped or not). This is
        # exactly RFC 9110 SS9.2.2's carve-out -- ancillary logging is allowed
        # to vary across idempotent calls -- so injecting real, growing
        # evidence here and confirming the verdict is unmoved is the
        # strongest form of this test, not a weaker one.
        create_events = _events_for(result.history, "create")
        assert len(create_events) == 2
        for index, event in enumerate(create_events, start=1):
            ancillary = observe_count_digest(state["logs"][:index], evidence=evidence, detail="ancillary logs")
            event.observer_results.append(ancillary)

        ancillary_counts = [
            next(r.value["count"] for r in event.observer_results if r.kind == "count_digest")
            for event in create_events
        ]
        assert ancillary_counts[0] < ancillary_counts[1]  # the ancillary channel really did grow between calls

        with_noise = classify_idempotency(result.history, subjects["create_item_dedup"])
        assert with_noise[0].status is PropertyStatus.observed_true
        assert with_noise[0].status == baseline[0].status


async def test_interrupt_attempt_that_never_takes_hold_yields_inconclusive(tmp_path) -> None:
    server, _state = build_stateful_server(response_delay=0.0)  # near-instant, so a generous budget never fires
    async with Client(server=server, mode="legacy", client_info=CLIENT_INFO) as client:
        bundle = await discovery_bundle_for(client)
        subjects = bundle["tool_subjects"]
        evidence = EvidenceStore(root=tmp_path)

        retry_step = ProbeStep(
            id="create",
            primitive=ProbePrimitive.RETRY_AFTER_INTERRUPT,
            tool_subject=subjects["create_item"],
            parameters={"arguments": {"name": "a"}, "interrupt_budget_ms": 5000},
        )
        read_back_step = ProbeStep(
            id="verify", primitive=ProbePrimitive.READ_BACK, tool_subject=subjects["list_items"], depends_on=["create"]
        )
        plan = _plan(bundle["server_subject"], [retry_step, read_back_step])

        result = await execute_plan(plan, client=client, discovery_bundle=bundle, evidence=evidence)

        create_events = _events_for(result.history, "create")
        assert all(event.status is InvocationStatus.completed for event in create_events)  # never actually interrupted
        transport_results = [r for event in create_events for r in event.observer_results if r.kind == "transport_interrupt"]
        assert transport_results and all(r.status is ObservationStatus.inconclusive for r in transport_results)

        claims = classify_idempotency(result.history, subjects["create_item"])
        assert len(claims) == 1
        assert claims[0].status is PropertyStatus.inconclusive
        assert "interrupt" in claims[0].detail.lower()


async def test_retry_after_genuine_interrupt_reveals_duplicate_creation(tmp_path) -> None:
    """End to end: a transport-level interrupt that genuinely fires (the
    SDK's own courtesy-cancel, confirmed via a real timeout race, never
    synthesized), followed by a naive retry, reproduces exactly the
    ambiguous-failure scenario Stripe's idempotency-key docs describe --
    and the read-back-driven classifier catches the resulting duplicate."""
    server, state = build_stateful_server(response_delay=0.2)
    async with Client(server=server, mode="legacy", client_info=CLIENT_INFO) as client:
        bundle = await discovery_bundle_for(client)
        subjects = bundle["tool_subjects"]
        evidence = EvidenceStore(root=tmp_path)

        retry_step = ProbeStep(
            id="create",
            primitive=ProbePrimitive.RETRY_AFTER_INTERRUPT,
            tool_subject=subjects["create_item"],
            parameters={"arguments": {"name": "a"}, "interrupt_budget_ms": 10},
        )
        read_back_step = ProbeStep(
            id="verify", primitive=ProbePrimitive.READ_BACK, tool_subject=subjects["list_items"], depends_on=["create"]
        )
        plan = _plan(bundle["server_subject"], [retry_step, read_back_step])

        result = await execute_plan(plan, client=client, discovery_bundle=bundle, evidence=evidence)

        create_events = _events_for(result.history, "create")
        assert len(create_events) == 2
        assert create_events[0].status is InvocationStatus.interrupted
        assert create_events[1].status is InvocationStatus.completed
        assert len(state["items"]) == 2  # the interrupted call's mutation landed anyway

        claims = classify_idempotency(result.history, subjects["create_item"])
        assert len(claims) == 1
        assert claims[0].status is PropertyStatus.observed_false
        assert claims[0].scope == "RETRY_AFTER_INTERRUPT"


async def test_parallel_double_fire_against_deduping_handler_collapses_to_one_item(tmp_path) -> None:
    server, state = build_stateful_server(response_delay=0.05)
    async with Client(server=server, mode="legacy", client_info=CLIENT_INFO) as client:
        bundle = await discovery_bundle_for(client)
        subjects = bundle["tool_subjects"]
        evidence = EvidenceStore(root=tmp_path)

        parallel_step = ProbeStep(
            id="create",
            primitive=ProbePrimitive.PARALLEL_DOUBLE_FIRE,
            tool_subject=subjects["create_item_dedup"],
            parameters={"arguments": {"name": "a"}, "count": 2, "interrupt_budget_ms": 5000},
        )
        read_back_step = ProbeStep(
            id="verify", primitive=ProbePrimitive.READ_BACK, tool_subject=subjects["list_items"], depends_on=["create"]
        )
        plan = _plan(bundle["server_subject"], [parallel_step, read_back_step])

        result = await execute_plan(plan, client=client, discovery_bundle=bundle, evidence=evidence)

        assert len(state["items"]) == 1
        create_events = _events_for(result.history, "create")
        assert len(create_events) == 2
        claims = classify_idempotency(result.history, subjects["create_item_dedup"])
        assert claims[0].status is PropertyStatus.observed_true
        assert claims[0].scope == "PARALLEL_DOUBLE_FIRE"


async def test_verify_with_timing_records_visibility_latency(tmp_path) -> None:
    server, _state = build_stateful_server()
    async with Client(server=server, mode="legacy", client_info=CLIENT_INFO) as client:
        bundle = await discovery_bundle_for(client)
        subjects = bundle["tool_subjects"]
        evidence = EvidenceStore(root=tmp_path)

        call_step = ProbeStep(
            id="create", primitive=ProbePrimitive.CALL_ONCE, tool_subject=subjects["create_item"], parameters={"arguments": {"name": "a"}}
        )
        timing_step = ProbeStep(
            id="timing",
            primitive=ProbePrimitive.VERIFY_WITH_TIMING,
            tool_subject=subjects["list_items"],
            depends_on=["create"],
            parameters={"delays_ms": [0]},
        )
        plan = _plan(bundle["server_subject"], [call_step, timing_step])

        result = await execute_plan(plan, client=client, discovery_bundle=bundle, evidence=evidence)

        timing_events = _events_for(result.history, "timing")
        assert len(timing_events) == 1  # visible on the very first poll, so the loop stops immediately
        summary = next(r for r in timing_events[-1].observer_results if r.kind == "timing_summary")
        assert summary.status is ObservationStatus.observed_true
        assert summary.value["visible"] is True
        assert isinstance(summary.value["elapsed_ms"], int)


async def test_unsupported_primitive_is_recorded_honestly_not_skipped(tmp_path) -> None:
    server, _state = build_stateful_server()
    async with Client(server=server, mode="legacy", client_info=CLIENT_INFO) as client:
        bundle = await discovery_bundle_for(client)
        subjects = bundle["tool_subjects"]
        evidence = EvidenceStore(root=tmp_path)

        step = ProbeStep(id="s", primitive=ProbePrimitive.PERTURB_AND_UNDO, tool_subject=subjects["create_item"])
        plan = _plan(bundle["server_subject"], [step])

        result = await execute_plan(plan, client=client, discovery_bundle=bundle, evidence=evidence)

        assert len(result.history.events) == 1
        event = result.history.events[0]
        assert event.status is InvocationStatus.error
        assert event.error is not None
        assert "PERTURB_AND_UNDO" in event.error
        assert "not implemented" in event.error


async def test_history_is_persisted_through_evidence_store(tmp_path) -> None:
    server, _state = build_stateful_server()
    async with Client(server=server, mode="legacy", client_info=CLIENT_INFO) as client:
        bundle = await discovery_bundle_for(client)
        subjects = bundle["tool_subjects"]
        evidence = EvidenceStore(root=tmp_path)

        step = ProbeStep(id="s", primitive=ProbePrimitive.CALL_ONCE, tool_subject=subjects["create_item"], parameters={"arguments": {"name": "a"}})
        plan = _plan(bundle["server_subject"], [step])

        result = await execute_plan(plan, client=client, discovery_bundle=bundle, evidence=evidence, run_id="run-1", clean_state_id="clean-1")

        assert result.history_digest == history_digest(result.history)
        stored = json.loads(evidence.get(result.history_digest))
        assert stored["run_id"] == "run-1"
        assert stored["clean_state_id"] == "clean-1"
        assert stored["target_subject"] == bundle["server_subject"]
        assert len(stored["events"]) == 1


async def test_sequence_is_monotonic_and_starts_at_zero(tmp_path) -> None:
    server, _state = build_stateful_server()
    async with Client(server=server, mode="legacy", client_info=CLIENT_INFO) as client:
        bundle = await discovery_bundle_for(client)
        subjects = bundle["tool_subjects"]
        evidence = EvidenceStore(root=tmp_path)

        repeat_step = ProbeStep(
            id="create", primitive=ProbePrimitive.REPEAT, tool_subject=subjects["create_item"], parameters={"arguments": {"name": "a"}, "count": 3}
        )
        plan = _plan(bundle["server_subject"], [repeat_step])

        result = await execute_plan(plan, client=client, discovery_bundle=bundle, evidence=evidence)

        sequences = [event.sequence for event in result.history.events]
        assert sequences == sorted(sequences)
        assert sequences == list(range(len(sequences)))


async def test_unknown_dependency_raises_before_any_call(tmp_path) -> None:
    server, state = build_stateful_server()
    async with Client(server=server, mode="legacy", client_info=CLIENT_INFO) as client:
        bundle = await discovery_bundle_for(client)
        subjects = bundle["tool_subjects"]
        evidence = EvidenceStore(root=tmp_path)

        step = ProbeStep(
            id="verify", primitive=ProbePrimitive.READ_BACK, tool_subject=subjects["list_items"], depends_on=["missing"]
        )
        plan = ProbePlan(subject_digest=bundle["server_subject"], steps=[step], cleanup_budget=0)

        with pytest.raises(ValueError, match="missing"):
            await execute_plan(plan, client=client, discovery_bundle=bundle, evidence=evidence)

        assert state["items"] == []  # nothing was ever called
