import base64

import pytest

from babi.attest.dsse import DSSE_PAYLOAD_TYPE_IN_TOTO, DsseError, Envelope, Signature, pae


def test_pae_matches_spec_test_vector() -> None:
    # https://github.com/secure-systems-lab/dsse/blob/master/protocol.md#test-vector
    result = pae("http://example.com/HelloWorld", b"hello world")
    assert result == b"DSSEv1 29 http://example.com/HelloWorld 11 hello world"


def test_pae_length_prefixes_are_byte_length_no_leading_zeros() -> None:
    # A multi-byte UTF-8 character must count its own byte length, not code-point count.
    result = pae("t\u00e9", b"x")  # "t\u00e9" is 3 UTF-8 bytes (t + 2-byte e-acute)
    assert result == b"DSSEv1 3 t\xc3\xa9 1 x"


def test_pae_empty_body() -> None:
    assert pae("application/json", b"") == b"DSSEv1 16 application/json 0 "


def test_pae_is_a_pure_function_of_its_inputs() -> None:
    assert pae("a", b"b") == pae("a", b"b")


def test_envelope_to_json_shape() -> None:
    envelope = Envelope(
        payload_type=DSSE_PAYLOAD_TYPE_IN_TOTO,
        body=b'{"hello":"world"}',
        signatures=(Signature(sig="c2ln", keyid="key-1"),),
    )
    doc = envelope.to_json()
    assert doc == {
        "payload": base64.standard_b64encode(b'{"hello":"world"}').decode("ascii"),
        "payloadType": DSSE_PAYLOAD_TYPE_IN_TOTO,
        "signatures": [{"sig": "c2ln", "keyid": "key-1"}],
    }


def test_envelope_to_json_omits_keyid_when_absent() -> None:
    envelope = Envelope(payload_type="text/plain", body=b"x", signatures=(Signature(sig="c2ln"),))
    doc = envelope.to_json()
    assert doc["signatures"] == [{"sig": "c2ln"}]


def test_envelope_round_trips_through_json() -> None:
    original = Envelope(
        payload_type=DSSE_PAYLOAD_TYPE_IN_TOTO,
        body=b"some binary-ish \x00\x01 content",
        signatures=(Signature(sig="c2lnMQ==", keyid="k1"), Signature(sig="c2lnMg==")),
    )
    restored = Envelope.from_json(original.to_json())
    assert restored == original
    assert restored.pae() == original.pae()


def test_envelope_pae_matches_module_level_pae() -> None:
    envelope = Envelope(payload_type="text/plain", body=b"hello world", signatures=())
    assert envelope.pae() == pae("text/plain", b"hello world")


@pytest.mark.parametrize(
    "broken",
    [
        {},
        {"payload": "x"},
        {"payload": "x", "payloadType": "y"},
        {"payload": "x", "payloadType": "y", "signatures": "not-a-list"},
        {"payload": 5, "payloadType": "y", "signatures": []},
        "not-a-dict",
        None,
    ],
)
def test_envelope_from_json_rejects_malformed_input(broken: object) -> None:
    with pytest.raises(DsseError):
        Envelope.from_json(broken)  # type: ignore[arg-type]


def test_envelope_from_json_rejects_invalid_base64_payload() -> None:
    with pytest.raises(DsseError, match="base64"):
        Envelope.from_json({"payload": "not valid base64!!", "payloadType": "x", "signatures": []})


def test_envelope_from_json_rejects_malformed_signature_entry() -> None:
    with pytest.raises(DsseError):
        Envelope.from_json({"payload": "aGk=", "payloadType": "x", "signatures": [{"keyid": "no-sig-field"}]})


def test_signature_from_json_keyid_optional() -> None:
    assert Signature.from_json({"sig": "c2ln"}) == Signature(sig="c2ln", keyid=None)
    assert Signature.from_json({"sig": "c2ln", "keyid": "k"}) == Signature(sig="c2ln", keyid="k")
