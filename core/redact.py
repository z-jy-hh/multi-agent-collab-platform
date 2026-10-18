"""N2: deterministic pre-persistence redaction (rules version n2-v2;
n2-v1 -> n2-v2, 2026-10-08: openai_sk gains a lookbehind so that
path substrings like "task-<16+ chars>" no longer false-positive;
real keys never sit glued to a letter).

Single masking function shared by every platform write choke point
(trace writer, op store, health report, run meta, run state). Purely
deterministic: the same input always yields the same output, and an
already-masked text is returned unchanged (no double masking).

Mask form: ``⟨REDACTED:<kind>:<sha256_8>⟩`` -- a mask PLUS a reconcilable
fingerprint of the original span; the plaintext itself is NOT preserved.

Rule set (n2-v2, deterministic, fixed order):
  openai_sk   ``sk-[A-Za-z0-9_-]{16,}``
  aws_akid    ``AKIA[0-9A-Z]{16}``
  pem         ``-----BEGIN ... PRIVATE KEY-----`` block through its END
  password    ``(?i)(password|passwd|pwd)\\s*[:=]\\s*\\S{6,}``
  token       ``(?i)(api[_-]?key|token|secret|bearer)\\s*[:=]\\s*\\S{12,}``
  phone_cn    ``1[3-9]\\d{9}`` (digit-bounded so it cannot bite into ID numbers)
  idcard_cn   ``\\d{17}[\\dXx]`` (digit-bounded)
  email       standard shape with an alphabetic TLD -- which inherently
              excludes ``name@1.1.0`` / ``crewai@0.18.0`` style ``@<numeric
              version>`` forms (the false-positive class measured in the
              decision card); an explicit negative lookbehind for a dot-led
              numeric tail is layered on top for belt and braces.

Switch: ``REDACTION_ENABLED`` -- ON unless explicitly ``"0"`` (owner
decision: default on).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

RULES_VERSION = "n2-v2"

_MASK_SPAN = re.compile(r"⟨REDACTED:[a-z_]+:[0-9a-f]{8}⟩")

# (kind, compiled regex) in fixed application order; single-pass, spans are
# resolved left-to-right, longest-first at equal start => deterministic.
_RULES: list[tuple[str, re.Pattern]] = [
    ("openai_sk", re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{16,}")),
    ("aws_akid", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("pem", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    ("password", re.compile(r"(?i)(?:password|passwd|pwd)\s*[:=]\s*\S{6,}")),
    ("token", re.compile(r"(?i)(?:api[_-]?key|token|secret|bearer)\s*[:=]\s*\S{12,}")),
    # alphanumeric boundaries (measured false-positive class: 11/18-digit
    # runs inside UUIDs and sha256 hex strings -- a bare digit boundary let
    # those through); a real CN phone/ID number is never glued to letters
    ("phone_cn", re.compile(r"(?<![0-9A-Za-z])1[3-9]\d{9}(?![0-9A-Za-z])")),
    ("idcard_cn", re.compile(r"(?<![0-9A-Za-z])\d{17}[\dXx](?![0-9A-Za-z])")),
    # alphabetic TLD required => @1.1.0 / @0.18.0 never match (R4 whitelist);
    # the lookbehind additionally refuses a numeric dot-tail before the TLD
    ("email", re.compile(
        r"[A-Za-z0-9._%+-]+@"
        r"(?![A-Za-z0-9.-]*\.\d)"
        r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")),
]


def redaction_enabled() -> bool:
    """Owner decision: default ON; only an explicit ``REDACTION_ENABLED=0``
    turns it off."""
    return os.environ.get("REDACTION_ENABLED", "1") != "0"


def _fingerprint(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()[:8]


def _mask_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _MASK_SPAN.finditer(text)]


def redact(text: str) -> tuple[str, dict]:
    """Mask every rule hit in ``text``. Returns ``(masked, counts_by_kind)``.

    Deterministic: same input -> byte-identical output; already-masked spans
    are never re-masked (idempotence, review criterion R6).
    """
    if not isinstance(text, str) or not text:
        return text, {}
    spans = _mask_spans(text)
    hits: list[tuple[int, int, str]] = []
    for kind, rx in _RULES:
        for m in rx.finditer(text):
            s, e = m.span()
            if any(s < pe and ps < e for ps, pe in spans):
                continue  # inside an existing mask: never re-mask
            hits.append((s, e, kind))
    if not hits:
        return text, {}
    # resolve overlaps deterministically: earlier start wins, then LONGER
    # span wins; contained matches inside an accepted span are dropped
    hits.sort(key=lambda t: (t[0], -(t[1] - t[0]), t[2]))
    accepted: list[tuple[int, int, str]] = []
    last_end = -1
    for s, e, kind in hits:
        if s < last_end:
            continue
        accepted.append((s, e, kind))
        last_end = e
    out: list[str] = []
    counts: dict[str, int] = {}
    pos = 0
    for s, e, kind in accepted:
        out.append(text[pos:s])
        plain = text[s:e]
        out.append(f"⟨REDACTED:{kind}:{_fingerprint(plain)}⟩")
        counts[kind] = counts.get(kind, 0) + 1
        pos = e
    out.append(text[pos:])
    return "".join(out), counts


def redact_strings_deep(obj):
    """Recursively redact every ``str`` VALUE inside dicts/lists/tuples.
    Keys, numbers and booleans are untouched. Returns
    ``(new_obj, counts_by_kind)``; non-str scalars pass through."""
    counts: dict[str, int] = {}

    def _walk(o):
        if isinstance(o, str):
            masked, c = redact(o)
            for k, v in c.items():
                counts[k] = counts.get(k, 0) + v
            return masked
        if isinstance(o, dict):
            return {k: _walk(v) for k, v in o.items()}
        if isinstance(o, list):
            return [_walk(v) for v in o]
        if isinstance(o, tuple):
            return tuple(_walk(v) for v in o)
        return o

    return _walk(obj), counts


def record_redaction(marker_path: Path, counts: dict) -> dict:
    """Merge ``counts`` into a redaction audit marker JSON (N2).

    Run artifacts use ``<run_dir>/redaction.json``; the op store (which is
    global, not run-scoped) uses ``redaction_ops.json`` next to its DB.
    Atomic tmp+replace; called ONLY when something actually matched, so a
    clean event costs zero extra IO."""
    import time
    p = Path(marker_path)
    payload: dict = {"rules_version": RULES_VERSION, "counts_by_kind": {},
                     "scanned_at": None}
    if p.exists():
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            payload = {"rules_version": RULES_VERSION, "counts_by_kind": {},
                       "scanned_at": None}
    merged = dict(payload.get("counts_by_kind") or {})
    for k, v in (counts or {}).items():
        merged[k] = int(merged.get(k, 0)) + int(v)
    payload["counts_by_kind"] = dict(sorted(merged.items()))
    payload["scanned_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload["rules_version"] = RULES_VERSION
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    os.replace(tmp, p)
    return payload


def record_run_redaction(run_dir: Path, counts: dict) -> dict:
    """Run-scoped audit marker (``<run_dir>/redaction.json``)."""
    return record_redaction(Path(run_dir) / "redaction.json", counts)
