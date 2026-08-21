import pytest

from babi.sandbox.credentials import MissingCredentialsError, missing_credentials, redact, require_credentials


def test_missing_credentials_reports_only_absent_names() -> None:
    assert missing_credentials(["A", "B", "C"], environ={"A": "1", "C": "3"}) == ("B",)


def test_missing_credentials_empty_when_all_present() -> None:
    assert missing_credentials(["A", "B"], environ={"A": "1", "B": "2"}) == ()


def test_missing_credentials_sorted_and_deduplicated() -> None:
    assert missing_credentials(["Z", "A", "A"], environ={}) == ("A", "Z")


def test_require_credentials_resolves_every_name() -> None:
    resolved = require_credentials(["A", "B"], environ={"A": "1", "B": "2", "C": "3"})
    assert resolved == {"A": "1", "B": "2"}


def test_require_credentials_raises_listing_missing_names() -> None:
    with pytest.raises(MissingCredentialsError) as excinfo:
        require_credentials(["A", "B"], environ={"A": "1"})
    assert excinfo.value.missing == ("B",)
    assert "B" in str(excinfo.value)


def test_require_credentials_never_partially_resolves() -> None:
    # Even though A is present, the whole call raises because B is not --
    # a caller can never receive a dict missing declared names silently.
    with pytest.raises(MissingCredentialsError):
        require_credentials(["A", "B"], environ={"A": "1"})


def test_redact_replaces_every_occurrence() -> None:
    text = "token=abc123 and again abc123 at the end"
    assert redact(text, ["abc123"]) == "token=<redacted> and again <redacted> at the end"


def test_redact_prefers_longer_secrets_first() -> None:
    # "ab" is a substring of "abcdef" -- redacting the shorter one first
    # would leave "cdef" exposed as a fragment of the real secret.
    text = "value is abcdef"
    result = redact(text, ["ab", "abcdef"])
    assert "cdef" not in result
    assert result == "value is <redacted>"


def test_redact_ignores_empty_secrets() -> None:
    assert redact("hello world", ["", "world"]) == "hello <redacted>"


def test_redact_is_noop_without_secrets() -> None:
    assert redact("nothing to hide", []) == "nothing to hide"
