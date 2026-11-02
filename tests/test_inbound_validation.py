"""Request-boundary validation — U2 LC-U2-3 (agent/inbound.py; ENT-202, BR3.1-BR3.5).

Untrusted input is bounded and rejected before any spend (FR4.6.1). The validator is pure:
it returns an InboundRequest or raises the internal `_Rejection(error_kind, detail,
request_id)`. The agent-level half (no router call, no Converse call, a record and a line
before RequestRejected) is in tests/test_request_path.py.

  T-VAL-U2-1  a list, a string and None as the payload are request_invalid, and the detail
              names "expected object" (BR3.1)
  T-VAL-U2-2  prompt missing, non-string, whitespace-only and a lone surrogate "\\ud800" are
              each request_invalid, each detail naming its condition (BR3.2)
  T-VAL-U2-3  a 102,400-byte ASCII prompt is accepted; 102,401 bytes is request_too_large; a
              multi-byte prompt over 102,400 bytes but under 102,400 characters is
              request_too_large - bytes, not characters (BR3.3, C1 R-22)
  T-VAL-U2-4  request_id absent, None or "" gives a generated 32-hex id; an integer is
              request_invalid; 257 characters is request_invalid; 256 is accepted (BR3.4, A-2)
  T-VAL-U2-5  the order is BR3.1 -> BR3.4 -> BR3.2 -> BR3.3: a bad request_id with a missing
              prompt fails on request_id; the first failure wins (BR3.5)
  T-VAL-U2-6  a 1 MB request_id: the detail is at most 512 characters with no 64-character
              substring of the value, and the rejection carries a generated 32-hex id
              (FR4.6.2, SD-U2-15)
  T-VAL-U2-7  keys other than prompt and request_id are ignored, not rejected (ENT-202)
  T-VAL-U2-8  InboundRequest.prompt_bytes equals the UTF-8 length, and the encoded bytes are
              carried for the post-window digest (LC-U2-3)
  T-VAL-U2-9  the autouse socket guard is active (NFR4.2)

Partition: stdlib and the package only. No SDK call, no account.
"""
from __future__ import annotations

import importlib
import re
import socket
import ssl  # noqa: F401 - loaded before the socket guard is installed (M-4, SD-U2-21)

import boto3  # noqa: F401 - loaded before the socket guard is installed (M-4, SD-U2-21)
import botocore.config  # noqa: F401 - as above
import pytest

HEX32 = re.compile(r"^[0-9a-f]{32}$")
BOUND = 102_400


class NetworkBlocked(RuntimeError):
    """Raised by the autouse guard: these tests must never open a socket."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise NetworkBlocked("tests/test_inbound_validation.py is offline by construction")

    monkeypatch.setattr(socket, "socket", _refuse)


def _inbound():
    return importlib.import_module("delphi.agent.inbound")


def _rejected(payload):
    inbound = _inbound()
    with pytest.raises(inbound._Rejection) as info:
        inbound.validate_inbound(payload)
    return info.value


def test_t_val_u2_1_payload_must_be_a_mapping():
    for payload in (["prompt", "hi"], "hi", None):
        r = _rejected(payload)
        assert r.error_kind == "request_invalid"
        assert "expected object" in r.detail
        assert HEX32.match(r.request_id)


def test_t_val_u2_2_prompt_must_be_a_non_empty_utf8_string():
    cases = {
        "missing": {"request_id": "r1"},
        "expected string": {"prompt": 42},
        "empty": {"prompt": "  \n\t "},
        "UTF-8": {"prompt": "half a pair \ud800 here"},
    }
    for needle, payload in cases.items():
        r = _rejected(payload)
        assert r.error_kind == "request_invalid", needle
        assert r.detail.startswith("prompt"), r.detail
        assert needle in r.detail, (needle, r.detail)


def test_t_val_u2_3_the_bound_is_102400_utf8_bytes_inclusive():
    inbound = _inbound()
    assert inbound.REQUEST_PROMPT_MAX_BYTES == BOUND
    assert inbound.validate_inbound({"prompt": "a" * BOUND}).prompt_bytes == BOUND
    over = _rejected({"prompt": "a" * (BOUND + 1)})
    assert over.error_kind == "request_too_large"
    assert over.detail == f"prompt is {BOUND + 1} bytes; bound is {BOUND}"
    multibyte = "é" * (BOUND // 2 + 1)  # 2 bytes each: 102,402 bytes, 51,201 characters
    assert len(multibyte) < BOUND < len(multibyte.encode("utf-8"))
    assert _rejected({"prompt": multibyte}).error_kind == "request_too_large"


def test_t_val_u2_4_request_id_is_generated_or_a_bounded_string():
    inbound = _inbound()
    for payload in ({"prompt": "hi"}, {"prompt": "hi", "request_id": None},
                    {"prompt": "hi", "request_id": ""}):
        assert HEX32.match(inbound.validate_inbound(payload).request_id)
    assert inbound.REQUEST_ID_MAX_CHARS == 256
    assert inbound.validate_inbound({"prompt": "hi", "request_id": "x" * 256}).request_id == (
        "x" * 256)
    for bad in (7, "x" * 257):
        r = _rejected({"prompt": "hi", "request_id": bad})
        assert r.error_kind == "request_invalid"
        assert r.detail.startswith("request_id")
        assert HEX32.match(r.request_id), "a rejected caller id is replaced, not echoed"


def test_t_val_u2_5_validation_order_first_failure_wins():
    r = _rejected({"request_id": 7})  # bad request_id AND a missing prompt
    assert r.detail.startswith("request_id"), r.detail
    r = _rejected({"request_id": "ok", "prompt": "a" * (BOUND + 1), "extra": 1})
    assert r.error_kind == "request_too_large"
    r = _rejected(["not", "a", "mapping"])
    assert r.detail.startswith("payload")


def test_t_val_u2_6_a_huge_request_id_is_never_echoed():
    value = "".join(chr(97 + (i * 7 + i // 26) % 26) for i in range(1 << 20))
    r = _rejected({"prompt": "hi", "request_id": value})
    assert r.error_kind == "request_invalid"
    assert len(r.detail) <= 512
    assert not any(r.detail[i:i + 64] in value for i in range(max(1, len(r.detail) - 63)))
    assert HEX32.match(r.request_id)


def test_t_val_u2_7_unknown_keys_are_ignored():
    req = _inbound().validate_inbound({"prompt": "hi", "request_id": "r1", "temperature": 2})
    assert (req.request_id, req.prompt) == ("r1", "hi")


def test_t_val_u2_8_prompt_bytes_and_encoded_bytes_are_carried():
    prompt = "Delphi ὄμφαλος"
    req = _inbound().validate_inbound({"prompt": prompt})
    assert req.encoded == prompt.encode("utf-8")
    assert req.prompt_bytes == len(prompt.encode("utf-8")) > len(prompt)


def test_t_val_u2_9_socket_guard_is_active():
    with pytest.raises(NetworkBlocked):
        socket.socket()
