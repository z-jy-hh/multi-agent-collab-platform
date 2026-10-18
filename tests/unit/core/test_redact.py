"""N2 unit tests: core/redact.py rules (n2-v1 -> n2-v2), whitelist,
idempotence.

All sample secrets here are SYNTHETIC demo forms living in tests/ (outside
the --check redaction scan scope by construction).
"""
from __future__ import annotations

import hashlib
import json

from core.redact import (RULES_VERSION, redact, redact_strings_deep,
                         redaction_enabled, record_run_redaction)


def _fp(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


def _mask(kind: str, plain: str) -> str:
    return f"⟨REDACTED:{kind}:{_fp(plain)}⟩"


SK = "sk-abcdef1234567890ghij"          # 20 class chars after sk-
PHONE = "13812345678"
IDCARD = "110101199001011234"
EMAIL = "zhangsan@example.com"


class TestRules:
    def test_openai_sk_masked(self):
        out, c = redact(f"key is {SK} in text")
        assert out == f"key is {_mask('openai_sk', SK)} in text"
        assert c == {"openai_sk": 1}

    def test_aws_akid_masked(self):
        ak = "AKIAIOSFODNN7EXAMPLE"  # 16 chars after AKIA
        out, c = redact(f"aws {ak} end")
        assert out == f"aws {_mask('aws_akid', ak)} end"
        assert c == {"aws_akid": 1}

    def test_pem_block_masked(self):
        pem = ("-----BEGIN RSA PRIVATE KEY-----\nMIIB\nabc\n"
               "-----END RSA PRIVATE KEY-----")
        out, c = redact(f"cert {pem} done")
        assert _mask("pem", pem) in out
        assert "MIIB" not in out
        assert c == {"pem": 1}

    def test_password_eq_masked(self):
        out, c = redact("password=hunter2secret tail")
        assert "hunter2secret" not in out
        assert c == {"password": 1}

    def test_token_eq_masked(self):
        out, c = redact("api_key=abcdefghijklmnop1234")
        assert "abcdefghijklmnop1234" not in out
        assert c == {"token": 1}

    def test_phone_masked(self):
        out, c = redact(f"call {PHONE} now")
        assert out == f"call {_mask('phone_cn', PHONE)} now"
        assert c == {"phone_cn": 1}

    def test_idcard_masked(self):
        out, c = redact(f"id {IDCARD} ok")
        assert out == f"id {_mask('idcard_cn', IDCARD)} ok"
        assert c == {"idcard_cn": 1}

    def test_email_masked(self):
        out, c = redact(f"mail {EMAIL} ok")
        assert out == f"mail {_mask('email', EMAIL)} ok"
        assert c == {"email": 1}


class TestSkLeftBoundary:
    """F-N1FIX1-1: the openai_sk lookbehind (?<![A-Za-z0-9]) must carry
    payload. Path-family substrings (sk- glued to a letter on its left)
    stay UNMASKED; every real-key context (line start / space / key= /
    quote) stays MASKED. Removing the lookbehind turns these red."""

    def test_path_substrings_not_masked(self):
        # the measured false-positive class: "ta|sk-|book-closed-loop" etc.
        for s in ("task-book-closed-loop",
                  "risk-abcdefghijklmnop123",
                  "mask-zyxwvutsrqponmlk"):
            out, c = redact(s)
            assert out == s, s
            assert c == {}, s

    def test_real_forms_still_masked(self):
        real = "sk-abcdef1234567890ghij"
        for ctx in (real,                    # line start
                    f"see {real} now",       # after a space
                    f"key={real}",           # after key=
                    f'"{real}"'):            # after a quote
            out, c = redact(ctx)
            assert real not in out, ctx
            assert "⟨REDACTED:openai_sk:" in out, ctx
            assert c == {"openai_sk": 1}, ctx

    def test_path_and_real_key_in_one_line(self):
        # strongest combined payload: exactly ONE mask (the real key), and
        # the path substring survives verbatim
        line = f"task-book-closed-loop and key={SK}"
        out, c = redact(line)
        assert out == f"task-book-closed-loop and key={_mask('openai_sk', SK)}"
        assert c == {"openai_sk": 1}


class TestWhitelistAndFalsePositives:
    """R4 + the measured false-positive class (decision card §1)."""

    def test_numeric_version_not_masked(self):
        for s in ("text-embedding@1.1.0", "pytest@1.7.10", "crewai@0.18.0"):
            out, c = redact(f"pins {s} today")
            assert out == f"pins {s} today", s
            assert c == {}

    def test_uuid_fragment_not_masked(self):
        # an 11-digit run inside a UUID hex tail must NOT read as a phone
        s = "run_id=9c3dbe12-0f20-4836-9db5-13812345678e"
        out, c = redact(s)
        assert out == s and c == {}

    def test_hex_hash_not_masked(self):
        # an 18-digit run inside a sha256 hex string must NOT read as an ID
        h = "d1395724138114898e908c6153dafe053c9a7dcc89ca8b6"
        out, c = redact(f"sha {h}")
        assert out == f"sha {h}" and c == {}

    def test_real_phone_still_masked_after_tightening(self):
        out, _ = redact("电话13812345678。")
        assert "13812345678" not in out


class TestIdempotenceAndDeterminism:
    """R6: same input -> byte-identical output; masked input is a fixpoint."""

    def test_deterministic(self):
        a = redact(f"{SK} {PHONE} {EMAIL} {IDCARD}")
        b = redact(f"{SK} {PHONE} {EMAIL} {IDCARD}")
        assert a == b

    def test_masked_input_is_fixpoint(self):
        once, _ = redact(f"{SK} {PHONE}")
        twice, c2 = redact(once)
        assert twice == once
        assert c2 == {}

    def test_mask_span_guard_vs_keyword_rules(self):
        # the password rule masks the WHOLE match (keyword + value); the mask
        # itself contains "password:" -- the mask-span guard must keep it a
        # fixpoint instead of re-masking on every pass
        once, _ = redact("password=supersecret99")
        assert once == _mask("password", "password=supersecret99")
        twice, c = redact(once)
        assert twice == once and c == {}


class TestDeepWalk:
    def test_dict_list_recursion(self):
        obj = {"task": f"call {PHONE}",
               "items": [f"mail {EMAIL}", {"nested": SK}],
               "n": 5, "flag": True}
        out, c = redact_strings_deep(obj)
        assert out["task"] == f"call {_mask('phone_cn', PHONE)}"
        assert out["items"][0] == f"mail {_mask('email', EMAIL)}"
        assert out["items"][1]["nested"] == _mask("openai_sk", SK)
        assert out["n"] == 5 and out["flag"] is True
        assert c == {"phone_cn": 1, "email": 1, "openai_sk": 1}
        # keys are untouched
        assert "task" in out and "items" in out

    def test_non_string_scalars_pass_through(self):
        out, c = redact_strings_deep({"a": 1.5, "b": None, "c": ["x", 3]})
        assert out == {"a": 1.5, "b": None, "c": ["x", 3]}
        assert c == {}


class TestSwitchAndMarker:
    def test_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("REDACTION_ENABLED", raising=False)
        assert redaction_enabled() is True

    def test_explicit_zero_disables(self, monkeypatch):
        monkeypatch.setenv("REDACTION_ENABLED", "0")
        assert redaction_enabled() is False

    def test_other_values_do_not_disable(self, monkeypatch):
        for v in ("1", "true", "yes", "on"):
            monkeypatch.setenv("REDACTION_ENABLED", v)
            assert redaction_enabled() is True, v

    def test_marker_merged_and_versioned(self, tmp_path):
        p1 = record_run_redaction(tmp_path, {"phone_cn": 2})
        p2 = record_run_redaction(tmp_path, {"phone_cn": 1, "email": 3})
        assert p2["rules_version"] == RULES_VERSION
        assert p2["counts_by_kind"] == {"email": 3, "phone_cn": 3}
        on_disk = json.loads((tmp_path / "redaction.json").read_text(
            encoding="utf-8"))
        assert on_disk["counts_by_kind"] == {"email": 3, "phone_cn": 3}
        assert p1 is not p2


class TestWriterWiring:
    """The trace choke point itself: EventJSONLWriter must persist the
    masked form and the per-run audit marker."""

    def test_event_writer_masks_payload_and_records(self, tmp_path):
        from core.events import EventJSONLWriter
        from core.schemas import Event

        w = EventJSONLWriter(tmp_path / "trace.jsonl")
        w(Event(run_id="n2-w", type="agent_output",
                payload={"text": f"reply contains {SK}"}))
        w.close()
        line = (tmp_path / "trace.jsonl").read_text(encoding="utf-8")
        assert SK not in line
        assert "⟨REDACTED:openai_sk:" in line
        marker = json.loads((tmp_path / "redaction.json").read_text(
            encoding="utf-8"))
        assert marker["counts_by_kind"] == {"openai_sk": 1}
        assert marker["rules_version"] == RULES_VERSION

    def test_event_writer_clean_payload_writes_no_marker(self, tmp_path):
        from core.events import EventJSONLWriter
        from core.schemas import Event

        w = EventJSONLWriter(tmp_path / "trace.jsonl")
        w(Event(run_id="n2-w", type="node_enter", payload={"node": "plan"}))
        w.close()
        assert not (tmp_path / "redaction.json").exists()
