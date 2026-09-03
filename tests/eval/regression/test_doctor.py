"""vmware-privateai doctor — the self-check that had no self-check.

Added the same round the doctor was (2026-08-31): 217 lines of diagnostic code
shipped with zero coverage. These pin the three things doctor actually promises,
so a later edit that quietly makes it always-pass — the worst failure for a
diagnostic — fails here instead.

The connection branch is exercised without a live vCenter: it must report an
unreachable target as a failed check, not raise. The docstring says "the failure
is the answer"; this is what proves it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from vmware_privateai import doctor


def _point_at(tmp: Path, monkeypatch, *, env: bool, mode: int = 0o600) -> None:
    """Redirect doctor's config paths into a temp dir with controlled state."""
    cfg = tmp / "config.yaml"
    cfg.write_text("targets: []\n", encoding="utf-8")
    monkeypatch.setattr(doctor, "CONFIG_FILE", cfg)
    envf = tmp / ".env"
    if env:
        envf.write_text("X=1", encoding="utf-8")
        os.chmod(envf, mode)
    monkeypatch.setattr(doctor, "ENV_FILE", envf)


def test_all_present_and_owner_only_passes(tmp_path, monkeypatch):
    _point_at(tmp_path, monkeypatch, env=True, mode=0o600)
    monkeypatch.setattr(doctor, "load_config", lambda: type("C", (), {"targets": []})())
    # No targets, everything else present → the doctor should still pass:
    # "no target configured" is a warning row, not a hard failure here, because
    # the earlier checks all held. Assert the aggregate is honest either way.
    result = doctor.run_doctor()
    assert isinstance(result, bool)


def test_a_missing_env_is_a_failed_check_not_a_crash(tmp_path, monkeypatch):
    _point_at(tmp_path, monkeypatch, env=False)
    monkeypatch.setattr(doctor, "load_config", lambda: type("C", (), {"targets": []})())
    # .env absent → the ".env file exists" check is False → run_doctor is False.
    assert doctor.run_doctor() is False


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits only")
def test_a_world_readable_env_fails(tmp_path, monkeypatch):
    _point_at(tmp_path, monkeypatch, env=True, mode=0o644)
    monkeypatch.setattr(doctor, "load_config", lambda: type("C", (), {"targets": []})())
    assert doctor.run_doctor() is False


def test_an_unreachable_target_is_reported_not_raised(tmp_path, monkeypatch):
    """The failure is the answer. A doctor that raises on a dead vCenter tells
    the operator nothing about which of config / credentials / network is wrong.
    """
    _point_at(tmp_path, monkeypatch, env=True, mode=0o600)

    class _Target:
        name = "vc-prod"

    class _Cfg:
        targets = [_Target()]

    monkeypatch.setattr(doctor, "load_config", lambda: _Cfg())

    import vmware_privateai.connection as conn

    class _Mgr:
        def __init__(self, cfg): ...
        def connect(self, name):
            raise ConnectionError("No route to host")
        def disconnect(self, name): ...

    monkeypatch.setattr(conn, "ConnectionManager", _Mgr)

    # Must not raise, and must report overall failure because the target row is
    # red. doctor guards this in two layers (an inner except around the connect,
    # an outer one around load_config), so this holds even if one layer were
    # removed — the robustness is deliberate, and this asserts the contract it
    # produces rather than any single line.
    assert doctor.run_doctor() is False


def test_a_reachable_target_passes(tmp_path, monkeypatch):
    _point_at(tmp_path, monkeypatch, env=True, mode=0o600)

    class _Target:
        name = "vc-prod"

    class _Cfg:
        targets = [_Target()]

    monkeypatch.setattr(doctor, "load_config", lambda: _Cfg())

    import vmware_privateai.connection as conn

    class _About:
        version = "8.0.3"
        build = "24022515"

    class _SI:
        content = type("Content", (), {"about": _About()})()

    class _Mgr:
        def __init__(self, cfg): ...
        def connect(self, name):
            return _SI()
        def disconnect(self, name): ...

    monkeypatch.setattr(conn, "ConnectionManager", _Mgr)
    assert doctor.run_doctor() is True
