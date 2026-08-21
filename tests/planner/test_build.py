from babi.plan import ProbePlan, ProbePrimitive, RationaleProvenance
from babi.planner.build import (
    PlanBuilderConstraints,
    build_probe_plan,
    disposable_scope_from_manifest,
    eligible_primitives,
)

_CREATE = {
    "name": "make_widget",
    "description": "Create a new widget resource in the workspace.",
    "inputSchema": {
        "type": "object",
        "properties": {"label": {"type": "string"}, "color": {"type": "string", "enum": ["red", "green", "blue"]}},
        "required": ["label", "color"],
    },
    "outputSchema": {"type": "object", "properties": {"widget_id": {"type": "string"}}, "required": ["widget_id"]},
}
_READ = {
    "name": "fetch_widget",
    "description": "Fetch a widget by its id.",
    "inputSchema": {"type": "object", "properties": {"widget_id": {"type": "string"}}, "required": ["widget_id"]},
}
_DELETE = {
    "name": "remove_widget",
    "description": "Permanently delete a widget by its id.",
    "inputSchema": {"type": "object", "properties": {"widget_id": {"type": "string"}}, "required": ["widget_id"]},
}
_CATALOG = [_CREATE, _READ, _DELETE]
_SUBJECTS = {
    "make_widget": "sha256:" + "a" * 64,
    "fetch_widget": "sha256:" + "b" * 64,
    "remove_widget": "sha256:" + "c" * 64,
}
_SERVER_SUBJECT = "sha256:" + "f" * 64


# --- eligible_primitives: the pure gating function -----------------------


def test_no_observer_no_disposable_scope_yields_only_always_safe_primitives() -> None:
    eligible = eligible_primitives(PlanBuilderConstraints())
    assert eligible == frozenset({ProbePrimitive.DISCOVER, ProbePrimitive.INSTANTIATE, ProbePrimitive.CALL_ONCE})


def test_observer_without_disposable_scope_unlocks_read_only_verification() -> None:
    eligible = eligible_primitives(PlanBuilderConstraints(observer_kinds=frozenset({"filesystem_diff"})))
    assert ProbePrimitive.READ_BACK in eligible
    assert ProbePrimitive.VERIFY_WITH_TIMING in eligible
    assert ProbePrimitive.CROSS_CALL_CONSISTENCY in eligible
    # PRECONDITION_REVOCATION requires *both* gates -- observer alone isn't enough.
    assert ProbePrimitive.PRECONDITION_REVOCATION not in eligible
    # These additionally require disposable scope -- still gated out.
    assert ProbePrimitive.REPEAT not in eligible
    assert ProbePrimitive.RETRY_AFTER_INTERRUPT not in eligible
    assert ProbePrimitive.PARALLEL_DOUBLE_FIRE not in eligible
    assert ProbePrimitive.PERTURB_AND_UNDO not in eligible
    assert ProbePrimitive.BOUNDARY_MUTATION_FUZZ not in eligible


def test_disposable_scope_without_observer_unlocks_only_boundary_fuzz() -> None:
    eligible = eligible_primitives(PlanBuilderConstraints(disposable_scope=True))
    assert ProbePrimitive.BOUNDARY_MUTATION_FUZZ in eligible
    # Everything else requiring disposable scope also requires an observer.
    assert ProbePrimitive.REPEAT not in eligible
    assert ProbePrimitive.RETRY_AFTER_INTERRUPT not in eligible
    assert ProbePrimitive.PARALLEL_DOUBLE_FIRE not in eligible
    assert ProbePrimitive.PERTURB_AND_UNDO not in eligible
    assert ProbePrimitive.PRECONDITION_REVOCATION not in eligible
    assert ProbePrimitive.READ_BACK not in eligible


def test_observer_and_disposable_scope_unlocks_every_primitive() -> None:
    eligible = eligible_primitives(
        PlanBuilderConstraints(observer_kinds=frozenset({"filesystem_diff"}), disposable_scope=True)
    )
    assert eligible == frozenset(ProbePrimitive)


def test_read_back_available_flag_satisfies_the_observer_gate_without_local_observer_kinds() -> None:
    eligible = eligible_primitives(PlanBuilderConstraints(read_back_available=True))
    assert ProbePrimitive.READ_BACK in eligible


def test_has_working_observer_is_true_for_either_signal() -> None:
    assert PlanBuilderConstraints().has_working_observer is False
    assert PlanBuilderConstraints(observer_kinds=frozenset({"network"})).has_working_observer is True
    assert PlanBuilderConstraints(read_back_available=True).has_working_observer is True


def test_disposable_scope_from_manifest_requires_both_prefix_and_deadline() -> None:
    class _Manifest:
        def __init__(self, resource_prefix=None, teardown_deadline=None):
            self.resource_prefix = resource_prefix
            self.teardown_deadline = teardown_deadline

    assert disposable_scope_from_manifest(None) is False
    assert disposable_scope_from_manifest(_Manifest()) is False
    assert disposable_scope_from_manifest(_Manifest(resource_prefix="abi-probe")) is False
    assert disposable_scope_from_manifest(_Manifest(teardown_deadline="2026-01-01T00:00:00Z")) is False
    assert disposable_scope_from_manifest(_Manifest("abi-probe", "2026-01-01T00:00:00Z")) is True


# --- build_probe_plan: end-to-end plan assembly ---------------------------


def test_first_iteration_is_call_once_only() -> None:
    """The Day 3-4 bar: with no constraints supplied at all, every step in
    the plan is DISCOVER/INSTANTIATE/CALL_ONCE -- never a probe that
    issues more than the one synthesized call per tool."""
    plan = build_probe_plan(_SERVER_SUBJECT, _CATALOG, _SUBJECTS, seed="v0")
    primitives = {step.primitive for step in plan.steps}
    assert primitives == {ProbePrimitive.DISCOVER, ProbePrimitive.INSTANTIATE, ProbePrimitive.CALL_ONCE}
    # One DISCOVER (whole-plan) + one INSTANTIATE/CALL_ONCE pair per tool.
    assert sum(1 for s in plan.steps if s.primitive is ProbePrimitive.DISCOVER) == 1
    assert sum(1 for s in plan.steps if s.primitive is ProbePrimitive.CALL_ONCE) == len(_CATALOG)


def test_plan_validates_as_probeplan() -> None:
    plan = build_probe_plan(_SERVER_SUBJECT, _CATALOG, _SUBJECTS, seed="v0")
    assert isinstance(plan, ProbePlan)
    reloaded = ProbePlan.model_validate(plan.model_dump(mode="json"))
    assert reloaded == plan


def test_plan_is_byte_identical_for_same_inputs() -> None:
    import json

    plan_a = build_probe_plan(_SERVER_SUBJECT, _CATALOG, _SUBJECTS, seed="v0", resource_prefix="abi-probe")
    plan_b = build_probe_plan(_SERVER_SUBJECT, _CATALOG, _SUBJECTS, seed="v0", resource_prefix="abi-probe")
    assert plan_a == plan_b
    assert json.dumps(plan_a.model_dump(mode="json"), sort_keys=True) == json.dumps(
        plan_b.model_dump(mode="json"), sort_keys=True
    )


def test_plan_changes_with_seed() -> None:
    plan_a = build_probe_plan(_SERVER_SUBJECT, _CATALOG, _SUBJECTS, seed="seed-a")
    plan_b = build_probe_plan(_SERVER_SUBJECT, _CATALOG, _SUBJECTS, seed="seed-b")
    args_a = next(s.parameters["arguments"] for s in plan_a.steps if s.primitive is ProbePrimitive.CALL_ONCE)
    args_b = next(s.parameters["arguments"] for s in plan_b.steps if s.primitive is ProbePrimitive.CALL_ONCE)
    assert args_a != args_b


def test_every_dependency_edge_has_rationale_evidence() -> None:
    """Matches babi.lint's check (b): every `depends_on` edge must be
    backed by a `rationale_evidence` entry."""
    plan = build_probe_plan(
        _SERVER_SUBJECT,
        _CATALOG,
        _SUBJECTS,
        seed="v0",
        constraints=PlanBuilderConstraints(observer_kinds=frozenset({"filesystem_diff"}), disposable_scope=True),
    )
    justified = {(entry.edge.from_step, entry.edge.to_step) for entry in plan.rationale_evidence}
    for step in plan.steps:
        for dependency in step.depends_on:
            assert (dependency, step.id) in justified, (dependency, step.id)


def test_all_rationale_provenance_is_schema_overlap_never_model_pass() -> None:
    """The deterministic builder never claims MODEL_PASS provenance --
    that provenance kind is reserved for edges a constrained model call
    itself proposes (model.py), never emitted here."""
    plan = build_probe_plan(
        _SERVER_SUBJECT,
        _CATALOG,
        _SUBJECTS,
        seed="v0",
        constraints=PlanBuilderConstraints(observer_kinds=frozenset({"filesystem_diff"}), disposable_scope=True),
    )
    assert plan.rationale_evidence  # there is something to check
    assert all(entry.provenance is RationaleProvenance.SCHEMA_OVERLAP for entry in plan.rationale_evidence)


def test_read_back_scheduled_when_observer_available_and_candidate_exists() -> None:
    plan = build_probe_plan(
        _SERVER_SUBJECT, _CATALOG, _SUBJECTS, seed="v0", constraints=PlanBuilderConstraints(read_back_available=True)
    )
    read_backs = [s for s in plan.steps if s.primitive is ProbePrimitive.READ_BACK]
    assert read_backs
    assert read_backs[0].tool_subject == _SUBJECTS["fetch_widget"]
    call_step = next(s for s in plan.steps if s.primitive is ProbePrimitive.CALL_ONCE and s.tool_subject == _SUBJECTS["make_widget"])
    assert read_backs[0].depends_on == [call_step.id]


def test_read_back_absent_without_observer_even_though_candidate_exists() -> None:
    plan = build_probe_plan(_SERVER_SUBJECT, _CATALOG, _SUBJECTS, seed="v0")
    assert not any(s.primitive is ProbePrimitive.READ_BACK for s in plan.steps)


def test_repeat_retry_parallel_perturb_scheduled_only_with_full_access() -> None:
    full_access = PlanBuilderConstraints(observer_kinds=frozenset({"filesystem_diff"}), disposable_scope=True)
    plan = build_probe_plan(_SERVER_SUBJECT, _CATALOG, _SUBJECTS, seed="v0", constraints=full_access)
    primitives = {s.primitive for s in plan.steps}
    assert ProbePrimitive.REPEAT in primitives
    assert ProbePrimitive.RETRY_AFTER_INTERRUPT in primitives
    assert ProbePrimitive.PARALLEL_DOUBLE_FIRE in primitives
    assert ProbePrimitive.PERTURB_AND_UNDO in primitives
    assert ProbePrimitive.VERIFY_WITH_TIMING in primitives


def test_repeat_retry_parallel_perturb_absent_with_observer_but_no_disposable_scope() -> None:
    observer_only = PlanBuilderConstraints(observer_kinds=frozenset({"filesystem_diff"}))
    plan = build_probe_plan(_SERVER_SUBJECT, _CATALOG, _SUBJECTS, seed="v0", constraints=observer_only)
    primitives = {s.primitive for s in plan.steps}
    assert ProbePrimitive.REPEAT not in primitives
    assert ProbePrimitive.RETRY_AFTER_INTERRUPT not in primitives
    assert ProbePrimitive.PARALLEL_DOUBLE_FIRE not in primitives
    assert ProbePrimitive.PERTURB_AND_UNDO not in primitives
    # READ_BACK/VERIFY_WITH_TIMING only need the observer, not disposable scope.
    assert ProbePrimitive.READ_BACK in primitives


def test_perturb_and_undo_requires_an_inverse_candidate() -> None:
    # A catalog with no DELETE-classed tool has no inverse candidate for
    # make_widget, so PERTURB_AND_UNDO must not appear even with full access.
    full_access = PlanBuilderConstraints(observer_kinds=frozenset({"filesystem_diff"}), disposable_scope=True)
    plan = build_probe_plan(_SERVER_SUBJECT, [_CREATE, _READ], _SUBJECTS, seed="v0", constraints=full_access)
    assert not any(s.primitive is ProbePrimitive.PERTURB_AND_UNDO for s in plan.steps)


def test_call_once_parameters_are_self_contained() -> None:
    """Per the executor consumption contract: a CALL_ONCE step must carry
    its own parameters["arguments"] rather than requiring the consumer to
    walk depends_on back to the INSTANTIATE step."""
    plan = build_probe_plan(_SERVER_SUBJECT, _CATALOG, _SUBJECTS, seed="v0")
    call_steps = [s for s in plan.steps if s.primitive is ProbePrimitive.CALL_ONCE]
    assert call_steps
    for step in call_steps:
        assert "arguments" in step.parameters
        assert isinstance(step.parameters["arguments"], dict)


def test_read_back_step_has_no_literal_argument_overrides_by_default() -> None:
    """Per the executor contract, the planner leaves READ_BACK arguments
    for the executor's live-data binding unless it deliberately overrides."""
    plan = build_probe_plan(
        _SERVER_SUBJECT, _CATALOG, _SUBJECTS, seed="v0", constraints=PlanBuilderConstraints(read_back_available=True)
    )
    read_backs = [s for s in plan.steps if s.primitive is ProbePrimitive.READ_BACK]
    assert read_backs
    assert all("arguments" not in step.parameters for step in read_backs)


def test_observers_field_reflects_constraints_verbatim() -> None:
    plan = build_probe_plan(
        _SERVER_SUBJECT,
        _CATALOG,
        _SUBJECTS,
        seed="v0",
        constraints=PlanBuilderConstraints(observer_kinds=frozenset({"network", "filesystem_diff"})),
    )
    assert plan.observers == ["filesystem_diff", "network"]


def test_generated_resources_tracks_synthesized_create_identifiers() -> None:
    plan = build_probe_plan(_SERVER_SUBJECT, _CATALOG, _SUBJECTS, seed="v0", resource_prefix="abi-probe")
    make_widget_call = next(s for s in plan.steps if s.tool_subject == _SUBJECTS["make_widget"] and s.primitive is ProbePrimitive.CALL_ONCE)
    # make_widget's inputSchema has no identifier-shaped required field, so
    # generated_resources falls back to the scope-marking resource_prefix.
    assert plan.generated_resources == ["abi-probe"]
    assert "label" in make_widget_call.parameters["arguments"]


def test_generated_resources_prefers_synthesized_identifier_values() -> None:
    creator_with_client_id = {
        "name": "make_ticket",
        "description": "Create a new ticket with a client-supplied id.",
        "inputSchema": {
            "type": "object",
            "properties": {"ticket_id": {"type": "string"}},
            "required": ["ticket_id"],
        },
    }
    subjects = {"make_ticket": "sha256:" + "9" * 64}
    plan = build_probe_plan("sha256:" + "e" * 64, [creator_with_client_id], subjects, seed="v0", resource_prefix="abi-probe")
    call_step = next(s for s in plan.steps if s.primitive is ProbePrimitive.CALL_ONCE)
    synthesized_id = call_step.parameters["arguments"]["ticket_id"]
    assert plan.generated_resources == [synthesized_id]


def test_typed_bindings_wire_create_output_identifier_into_later_tool_input() -> None:
    """Within one deterministic build, a later tool's identifier-shaped
    input reuses the value synthesized for an earlier CREATE tool's own
    identifier-shaped input (both share the same binding_key) -- fully
    deterministic, no live execution required."""
    plan = build_probe_plan(_SERVER_SUBJECT, _CATALOG, _SUBJECTS, seed="v0")
    make_call = next(s for s in plan.steps if s.tool_subject == _SUBJECTS["make_widget"] and s.primitive is ProbePrimitive.CALL_ONCE)
    fetch_call = next(s for s in plan.steps if s.tool_subject == _SUBJECTS["fetch_widget"] and s.primitive is ProbePrimitive.CALL_ONCE)
    remove_call = next(s for s in plan.steps if s.tool_subject == _SUBJECTS["remove_widget"] and s.primitive is ProbePrimitive.CALL_ONCE)
    # make_widget has no identifier-shaped input itself, so there is no
    # value to propagate -- but fetch_widget's and remove_widget's own
    # widget_id inputs still land on the exact same synthesized value as
    # each other (same binding_key, first writer wins deterministically).
    assert fetch_call.parameters["arguments"]["widget_id"] == remove_call.parameters["arguments"]["widget_id"]


def test_create_classed_tools_are_synthesized_before_others() -> None:
    """Ordering matters for typed-binding propagation: CREATE-classed
    tools' arguments (and any identifier values they mint) are synthesized
    before read/update/delete/act tools, never after."""
    creator_with_client_id = {
        "name": "make_ticket",
        "description": "Create a new ticket with a client-supplied id.",
        "inputSchema": {"type": "object", "properties": {"ticket_id": {"type": "string"}}, "required": ["ticket_id"]},
    }
    reader = {
        "name": "fetch_ticket",
        "description": "Fetch a ticket by its id.",
        "inputSchema": {"type": "object", "properties": {"ticket_id": {"type": "string"}}, "required": ["ticket_id"]},
    }
    subjects = {"make_ticket": "sha256:" + "9" * 64, "fetch_ticket": "sha256:" + "8" * 64}
    # Pass the reader first in catalog order -- the builder must still
    # process the creator first internally.
    plan = build_probe_plan("sha256:" + "e" * 64, [reader, creator_with_client_id], subjects, seed="v0")
    make_call = next(s for s in plan.steps if s.tool_subject == subjects["make_ticket"] and s.primitive is ProbePrimitive.CALL_ONCE)
    fetch_call = next(s for s in plan.steps if s.tool_subject == subjects["fetch_ticket"] and s.primitive is ProbePrimitive.CALL_ONCE)
    assert fetch_call.parameters["arguments"]["ticket_id"] == make_call.parameters["arguments"]["ticket_id"]


def test_default_constraints_is_none_and_behaves_like_explicit_defaults() -> None:
    plan_implicit = build_probe_plan(_SERVER_SUBJECT, _CATALOG, _SUBJECTS, seed="v0")
    plan_explicit = build_probe_plan(_SERVER_SUBJECT, _CATALOG, _SUBJECTS, seed="v0", constraints=PlanBuilderConstraints())
    assert plan_implicit == plan_explicit


def test_step_ids_never_contain_a_tool_name_literal() -> None:
    """Step ids are derived from catalog position + tool_subject digest,
    never from the tool's own name string."""
    plan = build_probe_plan(_SERVER_SUBJECT, _CATALOG, _SUBJECTS, seed="v0")
    for step in plan.steps:
        assert "widget" not in step.id
        assert "make" not in step.id
        assert "fetch" not in step.id
        assert "remove" not in step.id
