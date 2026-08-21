from babi.planner.heuristics import (
    HEURISTIC_TABLE,
    HeuristicDomain,
    OperationClass,
    ValueCategory,
    classify_operation,
    classify_value_category,
    token_jaccard,
    tokenize,
)


def test_tokenize_splits_snake_kebab_camel_and_digits() -> None:
    assert tokenize("create_directory") == ("create", "directory")
    assert tokenize("list-allowed-directories") == ("list", "allowed", "directories")
    assert tokenize("someCamelCaseThing2Foo") == ("some", "camel", "case", "thing", "2", "foo")
    assert tokenize("Read the file, please!") == ("read", "the", "file", "please")


def test_tokenize_ignores_none_and_empty_parts() -> None:
    assert tokenize(None, "", "widget") == ("widget",)
    assert tokenize() == ()


def test_classify_operation_reads_generic_create_verb() -> None:
    op_class, confidence = classify_operation("make_widget", "Create a new widget resource.")
    assert op_class is OperationClass.CREATE
    assert confidence > 0.5


def test_classify_operation_reads_generic_read_verb() -> None:
    op_class, confidence = classify_operation("fetch_widget", "Fetch a widget by its id.")
    assert op_class is OperationClass.READ
    assert confidence > 0.5


def test_classify_operation_reads_generic_delete_verb() -> None:
    op_class, _ = classify_operation("remove_widget", None)
    assert op_class is OperationClass.DELETE


def test_classify_operation_reads_generic_update_verb() -> None:
    op_class, _ = classify_operation("modify_widget", None)
    assert op_class is OperationClass.UPDATE


def test_classify_operation_reads_generic_act_verb() -> None:
    op_class, _ = classify_operation("dispatch_notification", None)
    assert op_class is OperationClass.ACT


def test_classify_operation_unknown_has_zero_confidence() -> None:
    op_class, confidence = classify_operation("frobnicate", "Do something opaque and undocumented.")
    assert op_class is OperationClass.UNKNOWN
    assert confidence == 0.0


def test_classify_operation_name_tokens_outweigh_description_tokens() -> None:
    # "create" in the name should win over a stray "removed" mention in
    # the description (name tokens are weighted double).
    op_class, _ = classify_operation("create_widget", "Ensures any previously removed widget is recreated.")
    assert op_class is OperationClass.CREATE


def test_classify_value_category_identifier() -> None:
    for name in ("widget_id", "uuid", "session_key", "auth_token", "user_ref"):
        assert classify_value_category(name) is ValueCategory.IDENTIFIER, name


def test_classify_value_category_path() -> None:
    assert classify_value_category("file_path") is ValueCategory.PATH
    assert classify_value_category("directory") is ValueCategory.PATH


def test_classify_value_category_email() -> None:
    assert classify_value_category("contact_email") is ValueCategory.EMAIL


def test_classify_value_category_timestamp() -> None:
    assert classify_value_category("created_at") is ValueCategory.TIMESTAMP
    assert classify_value_category("due_date") is ValueCategory.TIMESTAMP


def test_classify_value_category_generic_fallback() -> None:
    assert classify_value_category("color") is ValueCategory.GENERIC


def test_token_jaccard_ignores_shared_verbs() -> None:
    # Both tokenize to include "create"/"get"-family verbs; those must not
    # count toward similarity, only the shared "widget" noun should.
    similarity = token_jaccard(tokenize("create_widget"), tokenize("get_widget"))
    assert similarity > 0.0
    # A pair sharing *only* a verb (no common noun) must score zero.
    assert token_jaccard(tokenize("create_widget"), tokenize("get_gadget")) == 0.0


def test_token_jaccard_symmetric_and_bounded() -> None:
    a, b = tokenize("create_widget_resource"), tokenize("delete_widget_resource")
    forward = token_jaccard(a, b)
    backward = token_jaccard(b, a)
    assert forward == backward
    assert 0.0 <= forward <= 1.0


def test_token_jaccard_empty_inputs_is_zero() -> None:
    assert token_jaccard((), ("widget",)) == 0.0
    assert token_jaccard((), ()) == 0.0


def test_heuristic_table_is_the_single_declared_source() -> None:
    """Every operation-class and value-category rule the planner uses must
    come from this one table -- the requirement's "declared in one data
    table" bar, checked structurally rather than by convention alone."""
    assert len(HEURISTIC_TABLE) > 0
    domains = {rule.domain for rule in HEURISTIC_TABLE}
    assert domains == {HeuristicDomain.OPERATION, HeuristicDomain.VALUE}
    operation_categories = {rule.category for rule in HEURISTIC_TABLE if rule.domain is HeuristicDomain.OPERATION}
    assert operation_categories == {c.value for c in OperationClass if c is not OperationClass.UNKNOWN}
    value_categories = {rule.category for rule in HEURISTIC_TABLE if rule.domain is HeuristicDomain.VALUE}
    assert value_categories <= {c.value for c in ValueCategory}


def test_operation_rules_match_single_lowercase_english_verbs() -> None:
    """Every OPERATION-domain rule matches one whole token (`^(...)$`)
    against a plain lowercase English verb -- never a multi-word,
    underscore-joined operation name such as `create_issue`, the exact
    shape a real discovered MCP tool name takes (see tests/fixtures and
    evidence/). This is what makes the table incapable of colliding with
    the no-per-tool-logic literal-scan lint by construction."""
    for rule in HEURISTIC_TABLE:
        if rule.domain is not HeuristicDomain.OPERATION:
            continue
        assert rule.pattern.pattern.startswith("^(") and rule.pattern.pattern.endswith(")$")
        words = rule.pattern.pattern[2:-2].split("|")
        assert len(words) > 1, "expected several synonym verbs per category"
        for word in words:
            assert word.isalpha() and word.islower(), word


def test_heuristic_table_patterns_never_equal_a_realistic_discovered_tool_name() -> None:
    """No rule's raw pattern text is literally one of these multi-word,
    underscore-joined names -- the shape a real discovered MCP tool name
    takes (see tests/fixtures and evidence/)."""
    realistic_discovered_tool_names = {
        "create_issue",
        "get_issue",
        "delete_repository",
        "list_directory",
        "create_directory",
        "search_nodes",
        "read_text_file",
    }
    for rule in HEURISTIC_TABLE:
        for tool_name in realistic_discovered_tool_names:
            assert rule.pattern.pattern != tool_name
