"""R10-2: the vNNN width policy is DEFINED as ``\\d{3,}`` — the store's own
writer emits ``v{N:03d}``, which widens naturally past v999, so accepting 4+
digits is backward compatible; 1-2 digits and non-numeric names stay
rejected. Single source of truth: core.paths.version_dirs."""

from core.paths import version_dirs


def _mkdirs(base, names):
    for n in names:
        (base / n).mkdir(parents=True)


def test_three_digits_accepted(tmp_path):
    _mkdirs(tmp_path, ["v001", "v042", "v999"])
    assert [p.name for p in version_dirs(tmp_path)] == ["v001", "v042", "v999"]


def test_four_plus_digits_accepted(tmp_path):
    # the policy's whole point: histories that cross the thousand mark
    _mkdirs(tmp_path, ["v1000", "v1234"])
    assert [p.name for p in version_dirs(tmp_path)] == ["v1000", "v1234"]


def test_one_and_two_digits_rejected(tmp_path):
    _mkdirs(tmp_path, ["v1", "v01"])
    assert version_dirs(tmp_path) == []


def test_nonnumeric_and_hidden_names_rejected(tmp_path):
    _mkdirs(tmp_path, ["vX92_tmp", "v004_hidden", "v1x", "vx"])
    assert version_dirs(tmp_path) == []


def test_mixed_set_sorted_and_filtered(tmp_path):
    _mkdirs(tmp_path, ["v001", "v1", "v1234", "vX92_tmp", "v077", "v01"])
    assert [p.name for p in version_dirs(tmp_path)] == ["v001", "v077", "v1234"]


def test_files_never_listed(tmp_path):
    # a FILE named v001 is not a version directory
    (tmp_path / "v001").write_text("not a dir", encoding="utf-8")
    (tmp_path / "v002").mkdir()
    assert [p.name for p in version_dirs(tmp_path)] == ["v002"]


def test_v999999_accepted_no_upper_bound(tmp_path):
    # R11-2: the width policy has NO upper bound — six and seven digit
    # names are canonical. NOTE: only core is imported here — this file is
    # part of the mutation probe's pristine subset (core/ + tests/unit/
    # fresh copy, no evolver/), so the VersionStore.next_version()
    # derivation lives in tests/unit/evolver/test_versions.py instead.
    _mkdirs(tmp_path, ["v999999", "v1000000"])
    assert [p.name for p in version_dirs(tmp_path)] == ["v1000000", "v999999"]
