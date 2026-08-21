from babi.planner.dependencies import (
    CandidateKind,
    infer_all_candidates,
    infer_inverse_candidates,
    infer_producer_consumer_candidates,
    infer_read_back_candidates,
)
from babi.planner.schema import analyze_catalog

_CREATE = {
    "name": "make_widget",
    "description": "Create a new widget resource.",
    "inputSchema": {"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"]},
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
_UNRELATED = {
    "name": "list_reports",
    "description": "List quarterly financial reports.",
    "inputSchema": {"type": "object", "properties": {"quarter": {"type": "string"}}, "required": ["quarter"]},
}
_SUBJECTS = {
    "make_widget": "sha256:" + "a" * 64,
    "fetch_widget": "sha256:" + "b" * 64,
    "remove_widget": "sha256:" + "c" * 64,
    "list_reports": "sha256:" + "d" * 64,
}


def _catalog(*tools):
    return analyze_catalog(list(tools), _SUBJECTS)


def test_read_back_candidate_found_between_create_and_read() -> None:
    candidates = infer_read_back_candidates(_catalog(_CREATE, _READ))
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.kind is CandidateKind.READ_BACK
    assert candidate.from_tool_subject == _SUBJECTS["make_widget"]
    assert candidate.to_tool_subject == _SUBJECTS["fetch_widget"]
    assert 0.0 < candidate.score <= 1.0
    assert candidate.evidence  # never an unevidenced assertion


def test_read_back_candidate_absent_for_unrelated_tool() -> None:
    candidates = infer_read_back_candidates(_catalog(_CREATE, _UNRELATED))
    assert candidates == ()


def test_read_back_candidate_found_for_no_argument_list_output() -> None:
    list_tool = {
        "name": "list_widgets",
        "description": "List widget resources.",
        "inputSchema": {"type": "object", "properties": {}},
        "outputSchema": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"widget_id": {"type": "string"}},
                "required": ["widget_id"],
            },
        },
    }
    subjects = dict(_SUBJECTS, list_widgets="sha256:" + "e" * 64)
    candidates = infer_read_back_candidates(analyze_catalog([_CREATE, list_tool], subjects))
    assert len(candidates) == 1
    assert candidates[0].from_tool_subject == _SUBJECTS["make_widget"]
    assert candidates[0].to_tool_subject == subjects["list_widgets"]
    assert any("output identifiers" in evidence for evidence in candidates[0].evidence)

def test_read_back_requires_a_read_classed_consumer() -> None:
    # Two CREATE-classed tools should never pair as read-back candidates.
    other_create = {
        "name": "make_gadget",
        "description": "Create a new gadget resource.",
        "inputSchema": {"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"]},
    }
    candidates = infer_read_back_candidates(_catalog(_CREATE, other_create))
    assert candidates == ()


def test_producer_consumer_candidate_from_output_to_input_field() -> None:
    candidates = infer_producer_consumer_candidates(_catalog(_CREATE, _READ))
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.kind is CandidateKind.PRODUCER_CONSUMER
    assert candidate.from_field == ("widget_id",)
    assert candidate.to_field == ("widget_id",)
    assert candidate.score > 0.5
    assert any("binding key" in item for item in candidate.evidence)


def test_producer_consumer_only_matches_required_consumer_fields() -> None:
    optional_consumer = {
        "name": "search_widgets",
        "description": "Search widgets, optionally filtered by id.",
        "inputSchema": {"type": "object", "properties": {"widget_id": {"type": "string"}}, "required": []},
    }
    subjects = dict(_SUBJECTS, search_widgets="sha256:" + "e" * 64)
    candidates = infer_producer_consumer_candidates(analyze_catalog([_CREATE, optional_consumer], subjects))
    assert candidates == ()


def test_producer_consumer_keeps_only_best_field_per_consumer_field() -> None:
    producer_two_fields = {
        "name": "make_widget",
        "description": "Create a new widget resource.",
        "inputSchema": {"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"]},
        "outputSchema": {
            "type": "object",
            "properties": {"widget_id": {"type": "string"}, "owner_id": {"type": "string"}},
            "required": ["widget_id", "owner_id"],
        },
    }
    candidates = infer_producer_consumer_candidates(_catalog(producer_two_fields, _READ))
    # Exactly one candidate per (producer, consumer, to_field) triple --
    # "widget_id" should win over "owner_id" (exact name+binding match).
    assert len(candidates) == 1
    assert candidates[0].from_field == ("widget_id",)


def test_inverse_candidate_between_create_and_delete() -> None:
    candidates = infer_inverse_candidates(_catalog(_CREATE, _DELETE))
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.kind is CandidateKind.INVERSE
    assert candidate.from_tool_subject == _SUBJECTS["make_widget"]
    assert candidate.to_tool_subject == _SUBJECTS["remove_widget"]
    assert candidate.evidence


def test_inverse_candidate_absent_without_create_delete_pair() -> None:
    assert infer_inverse_candidates(_catalog(_CREATE, _READ)) == ()
    assert infer_inverse_candidates(_catalog(_READ, _DELETE)) == ()


def test_inverse_candidate_for_unrelated_resource_domains_scores_low() -> None:
    """Two tools that share only the generic `string:none:identifier`
    binding key (near-universal for a bare-string id field) but no
    resource-noun lexical overlap must still be *scored*, never silently
    dropped -- but low enough to sit at/below `build.py`'s
    `_MIN_CANDIDATE_SCORE` acceptance floor (0.3), so the builder does not
    wire together two unrelated resource domains just because both use
    plain string ids."""
    unrelated_delete = {
        "name": "remove_report",
        "description": "Delete a financial report.",
        "inputSchema": {"type": "object", "properties": {"report_id": {"type": "string"}}, "required": ["report_id"]},
    }
    subjects = dict(_SUBJECTS, remove_report="sha256:" + "e" * 64)
    unrelated_candidates = infer_inverse_candidates(analyze_catalog([_CREATE, unrelated_delete], subjects))
    genuine_candidates = infer_inverse_candidates(_catalog(_CREATE, _DELETE))

    assert len(unrelated_candidates) == 1
    assert unrelated_candidates[0].score <= 0.3
    assert len(genuine_candidates) == 1
    assert genuine_candidates[0].score > unrelated_candidates[0].score


def test_candidates_are_score_ranked_descending() -> None:
    candidates = infer_read_back_candidates(_catalog(_CREATE, _READ, _UNRELATED))
    scores = [c.score for c in candidates]
    assert scores == sorted(scores, reverse=True)


def test_infer_all_candidates_aggregates_every_kind() -> None:
    result = infer_all_candidates(_catalog(_CREATE, _READ, _DELETE, _UNRELATED))
    assert len(result.catalog) == 4
    assert len(result.read_back) >= 1
    assert len(result.producer_consumer) >= 1
    assert len(result.inverse) >= 1


def test_deterministic_candidates_filters_by_tool_subject_and_min_score() -> None:
    result = infer_all_candidates(_catalog(_CREATE, _READ, _DELETE))
    read_back_for_creator = result.read_back_for(_SUBJECTS["make_widget"])
    assert all(c.from_tool_subject == _SUBJECTS["make_widget"] for c in read_back_for_creator)
    assert result.read_back_for(_SUBJECTS["make_widget"], min_score=2.0) == ()  # impossibly high bar
    assert result.inverse_for(_SUBJECTS["make_widget"])
    assert result.producer_consumer_for(_SUBJECTS["fetch_widget"])


def test_to_json_is_plain_json_serializable_with_fractional_scores() -> None:
    import json

    result = infer_all_candidates(_catalog(_CREATE, _READ, _DELETE))
    payload = result.to_json()
    encoded = json.dumps(payload)  # must not raise
    assert "read_back" in payload
    assert "producer_consumer" in payload
    assert "inverse" in payload
    decoded = json.loads(encoded)
    assert decoded == payload


def test_every_rationale_kind_is_schema_derived_never_a_bare_assertion() -> None:
    # Every candidate must carry either structural or lexical evidence (or
    # both) -- never an unjustified edge.
    result = infer_all_candidates(_catalog(_CREATE, _READ, _DELETE, _UNRELATED))
    for bucket in (result.read_back, result.producer_consumer, result.inverse):
        for candidate in bucket:
            assert len(candidate.evidence) > 0
            assert any(
                text.startswith("schema-overlap") or text.startswith("lexical-token-similarity")
                for text in candidate.evidence
            )
