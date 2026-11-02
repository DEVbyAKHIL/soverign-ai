from pathlib import Path

import pytest

from gateway.config import get_settings
from policy.loader import PolicyLoadError, load_policy_dir

POLICY = """policy_id: {pid}
version: {ver}
description: d
severity: {sev}
enabled: {enabled}
match:
  destination_jurisdiction: [non_eu]
actions: [deny]
"""


def _write(d: Path, name: str, pid: str, ver="1.0.0", sev="high", enabled="true") -> None:
    (d / name).write_text(
        POLICY.format(pid=pid, ver=ver, sev=sev, enabled=enabled), encoding="utf-8"
    )


def test_repository_policies_load():
    ps = load_policy_dir(get_settings().policy_dir)
    ids = {p.policy_id for p in ps.policies}
    assert ids == {
        "block_pii_to_non_eu_models",
        "require_review_high_risk",
        "block_system_prompt_extraction",
        "route_confidential_to_eu",
    }
    assert len(ps.version) == 16


def test_ordering_is_by_severity_then_id(tmp_path):
    _write(tmp_path, "a.yaml", "zzz_low", sev="low")
    _write(tmp_path, "b.yaml", "bbb_crit", sev="critical")
    _write(tmp_path, "c.yaml", "aaa_crit", sev="critical")
    ps = load_policy_dir(tmp_path)
    assert [p.policy_id for p in ps.policies] == ["aaa_crit", "bbb_crit", "zzz_low"]


def test_version_changes_when_content_changes(tmp_path):
    _write(tmp_path, "a.yaml", "pol_a")
    v1 = load_policy_dir(tmp_path).version
    assert load_policy_dir(tmp_path).version == v1  # stable
    _write(tmp_path, "a.yaml", "pol_a", ver="1.0.1")
    assert load_policy_dir(tmp_path).version != v1


def test_disabled_policy_skipped(tmp_path):
    _write(tmp_path, "a.yaml", "pol_a")
    _write(tmp_path, "b.yaml", "pol_b", enabled="false")
    assert [p.policy_id for p in load_policy_dir(tmp_path).policies] == ["pol_a"]


def test_one_invalid_file_blocks_everything(tmp_path):
    _write(tmp_path, "a.yaml", "pol_a")
    (tmp_path / "b.yaml").write_text("policy_id: broken\n", encoding="utf-8")
    with pytest.raises(PolicyLoadError) as exc:
        load_policy_dir(tmp_path)
    assert "b.yaml" in str(exc.value)


def test_duplicate_ids_rejected(tmp_path):
    _write(tmp_path, "a.yaml", "pol_a")
    _write(tmp_path, "b.yaml", "pol_a", ver="2.0.0")
    with pytest.raises(PolicyLoadError, match="duplicate"):
        load_policy_dir(tmp_path)


def test_empty_or_missing_dir_rejected(tmp_path):
    with pytest.raises(PolicyLoadError, match="no enabled"):
        load_policy_dir(tmp_path)
    with pytest.raises(PolicyLoadError, match="not found"):
        load_policy_dir(tmp_path / "nope")
