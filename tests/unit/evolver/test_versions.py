"""R9-D3: duplicate / mismatched version records must turn the consistency
check RED (GuardViolation) — closing the round-8 review residual #4, which
was only claimed, never demonstrated.

The duplicate scenario in a vNNN world: two canonical directories cannot share
a name, so a duplicate record version ALWAYS manifests as (at least) one
directory whose record version mismatches its directory number — both
branches are exercised below.
"""

import json

import pytest

from core.errors import GuardViolation
from evolver.versions import check_record_consistency


def _write_record(base, dirname, version):
    vdir = base / dirname
    vdir.mkdir(parents=True)
    (vdir / "record.json").write_text(
        json.dumps({"version": version, "reason": "inject"}), encoding="utf-8")
    return vdir


def test_consistent_history_green(tmp_path):
    for i in (1, 2, 3):
        _write_record(tmp_path, f"v{i:03d}", i)
    check_record_consistency(tmp_path)  # must NOT raise


def test_duplicate_record_version_turns_red(tmp_path):
    # v002 claims version 2; v003's record ALSO claims version 2
    # -> duplicate (visible as the v003 mismatch)
    _write_record(tmp_path, "v001", 1)
    _write_record(tmp_path, "v002", 2)
    _write_record(tmp_path, "v003", 2)
    with pytest.raises(GuardViolation, match="inconsistent"):
        check_record_consistency(tmp_path)


def test_record_version_mismatch_turns_red(tmp_path):
    _write_record(tmp_path, "v001", 1)
    _write_record(tmp_path, "v004", 99)  # dir v004, record says 99
    with pytest.raises(GuardViolation, match="v004"):
        check_record_consistency(tmp_path)


def test_missing_record_is_tolerated(tmp_path):
    # a canonical dir without record.json is skipped (same semantics as the
    # endpoint and backfill); it is NOT a consistency violation
    _write_record(tmp_path, "v001", 1)
    (tmp_path / "v002").mkdir()
    check_record_consistency(tmp_path)  # must NOT raise


def test_noncanonical_dirs_are_out_of_scope(tmp_path):
    # vX92_tmp / v1 / v1x are not canonical: enumeration (and hence this
    # check) never sees them — that is the R9-2 tightening, asserted here so
    # the check cannot silently regress to the loose glob
    _write_record(tmp_path, "v001", 1)
    _write_record(tmp_path, "vX92_tmp", 92)
    _write_record(tmp_path, "v1", 1)
    check_record_consistency(tmp_path)  # must NOT raise (rogue dirs invisible)


# --- R10-1: the write side itself is gated (residual R1) --------------------
# The consistency check used to run only at the backfill() entry, so a bad
# record written through VersionStore.save_record stayed invisible until the
# next maintenance pass. Now save_record refuses BEFORE any bytes move, so a
# refused write leaves no half-product behind.

from evolver.versions import VersionStore  # noqa: E402


def _snap(tmp_path, version):
    """The real flow: the version dir exists (snapshot() creates it when it
    copies at least one file) BEFORE save_record writes the record once into
    it. Reproduce that precondition here."""
    (tmp_path / f"v{version:03d}").mkdir(parents=True)


def test_write_side_refuses_duplicate_record_version(tmp_path):
    # v002 already carries record version 2 (write it the normal way first)
    store = VersionStore(base_dir=tmp_path)
    _snap(tmp_path, 1)
    _snap(tmp_path, 2)
    store.save_record(1, "r1", "KEEP", 0.1, {})
    store.save_record(2, "r2", "ROLLBACK", -0.1, {})
    original_bytes = (tmp_path / "v002" / "record.json").read_bytes()
    with pytest.raises(GuardViolation, match="already carries record version 2"):
        store.save_record(2, "intruder", "KEEP", 0.0, {})
    # no half-product: the existing record is byte-identical (never touched)
    assert (tmp_path / "v002" / "record.json").read_bytes() == original_bytes


def test_write_side_refuses_to_extend_broken_history(tmp_path):
    # a record whose version mismatches its directory (written out-of-band)
    # poisons the history; save_record must refuse, not extend it
    store = VersionStore(base_dir=tmp_path)
    _snap(tmp_path, 1)
    _snap(tmp_path, 2)
    store.save_record(1, "r1", "KEEP", 0.1, {})
    _write_record(tmp_path, "v004", 99)  # dir v004, record says 99
    original_bytes = (tmp_path / "v004" / "record.json").read_bytes()
    with pytest.raises(GuardViolation, match="inconsistent"):
        store.save_record(2, "r2", "KEEP", 0.0, {})
    # the poisoned record is untouched, and NO new record was written
    assert (tmp_path / "v004" / "record.json").read_bytes() == original_bytes
    assert not (tmp_path / "v002" / "record.json").exists()


def test_write_side_allows_fresh_appends(tmp_path):
    # the normal flow (dir exists, record written exactly once) keeps working
    store = VersionStore(base_dir=tmp_path)
    _snap(tmp_path, 1)
    _snap(tmp_path, 2)
    p1 = store.save_record(1, "r1", "KEEP", 0.1, {})
    p2 = store.save_record(2, "r2", "ROLLBACK", -0.1, {})
    assert p1.exists() and p2.exists()
    check_record_consistency(tmp_path)  # must NOT raise


# --- R11-1: the fresh-store crash path is closed -----------------------------
# Previously save_record assumed the version dir existed (created only by
# snapshot() when it copies at least one file) — a fresh store + direct
# save_record crashed with FileNotFoundError (demonstrated by the R10
# review). Now the directory is created on demand; append-only is untouched.

def test_fresh_store_direct_save_record_succeeds(tmp_path):
    # NO snapshot, NO pre-made dir: the exact crash scenario from the R10 review
    store = VersionStore(base_dir=tmp_path)
    assert store.next_version() == 1  # nothing enumerated yet
    p = store.save_record(1, "fresh store, no dir", "KEEP", 0.2, {})
    assert p.exists()
    rec = json.loads(p.read_text(encoding="utf-8"))
    assert rec["version"] == 1
    assert rec["reason"] == "fresh store, no dir"
    assert rec["decision"] == "KEEP"
    check_record_consistency(tmp_path)  # the on-demand dir is canonical


def test_fresh_store_duplicate_save_record_still_refused(tmp_path):
    # append-only is NOT weakened by the on-demand mkdir: the second write
    # to the same version must still hit the duplicate guard
    store = VersionStore(base_dir=tmp_path)
    store.save_record(1, "first", "KEEP", 0.1, {})
    original_bytes = (tmp_path / "v001" / "record.json").read_bytes()
    with pytest.raises(GuardViolation, match="already carries record version 1"):
        store.save_record(1, "intruder", "ROLLBACK", 0.0, {})
    assert (tmp_path / "v001" / "record.json").read_bytes() == original_bytes


def test_v999999_next_version_derives_1000000(tmp_path):
    # R11-2: width policy has NO upper bound — next_version() derives
    # max+1 = 1000000, whose writer name (v{N:03d} -> v1000000) is itself
    # seven digits and canonical. Lives in the evolver tree (not
    # tests/unit/core) because it imports evolver: the mutation probe's
    # pristine subset copies core/ + tests/unit/ only. tmp store only:
    # the real prompts/.versions is never touched.
    from core.paths import version_dirs

    (tmp_path / "v999999").mkdir(parents=True)
    assert [p.name for p in version_dirs(tmp_path)] == ["v999999"]
    store = VersionStore(base_dir=tmp_path)
    assert store.next_version() == 1_000_000
    assert f"v{store.next_version():03d}" == "v1000000"  # writer widens on its own
