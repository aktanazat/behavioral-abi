from babi import canon, subject


def test_server_subject_format() -> None:
    digest = subject.server_subject(
        registry_name="filesystem",
        registry_version="1.0.0",
        package_or_endpoint="@modelcontextprotocol/server-filesystem",
        runtime_artifact_digest=None,
        protocol_revision="2026-07-28",
    )
    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64


def test_server_subject_deterministic() -> None:
    kwargs = dict(
        registry_name="filesystem",
        registry_version="1.0.0",
        package_or_endpoint="@modelcontextprotocol/server-filesystem",
        runtime_artifact_digest=None,
        protocol_revision="2026-07-28",
    )
    assert subject.server_subject(**kwargs) == subject.server_subject(**kwargs)


def test_server_subject_none_fields_are_explicit_nulls_not_empty_strings() -> None:
    with_none = subject.server_subject(
        registry_name=None,
        registry_version=None,
        package_or_endpoint="pkg",
        runtime_artifact_digest=None,
        protocol_revision="rev",
    )
    with_empty = subject.server_subject(
        registry_name="",
        registry_version="",
        package_or_endpoint="pkg",
        runtime_artifact_digest="",
        protocol_revision="rev",
    )
    assert with_none != with_empty


def test_server_subject_injective_across_field_boundaries() -> None:
    # Naive concatenation of ("ab", "c") and ("a", "bc") would collide;
    # canonical-JSON-wrapping must not.
    first = subject.server_subject(
        registry_name="ab",
        registry_version="c",
        package_or_endpoint="pkg",
        runtime_artifact_digest=None,
        protocol_revision="rev",
    )
    second = subject.server_subject(
        registry_name="a",
        registry_version="bc",
        package_or_endpoint="pkg",
        runtime_artifact_digest=None,
        protocol_revision="rev",
    )
    assert first != second


def test_server_subject_sensitive_to_every_field() -> None:
    base = dict(
        registry_name="n",
        registry_version="v",
        package_or_endpoint="pkg",
        runtime_artifact_digest="d",
        protocol_revision="rev",
    )
    baseline = subject.server_subject(**base)
    for field in base:
        mutated = dict(base)
        mutated[field] = "different"
        assert subject.server_subject(**mutated) != baseline


def test_tool_subject_scoped_to_server_subject() -> None:
    server_a = subject.server_subject("n", "v", "pkg-a", None, "rev")
    server_b = subject.server_subject("n", "v", "pkg-b", None, "rev")
    tool_def = {"name": "some_tool", "inputSchema": {"type": "object"}}

    assert subject.tool_subject(server_a, tool_def) != subject.tool_subject(server_b, tool_def)


def test_tool_subject_sensitive_to_tool_definition() -> None:
    server = subject.server_subject("n", "v", "pkg", None, "rev")
    tool_a = {"name": "t", "inputSchema": {"type": "object"}}
    tool_b = {"name": "t", "inputSchema": {"type": "string"}}
    assert subject.tool_subject(server, tool_a) != subject.tool_subject(server, tool_b)


def test_tool_subject_matches_manual_canonical_wrap() -> None:
    server = subject.server_subject("n", "v", "pkg", None, "rev")
    tool_def = {"name": "t"}
    expected = canon.digest({"server_subject": server, "tool_definition": tool_def})
    assert subject.tool_subject(server, tool_def) == expected
