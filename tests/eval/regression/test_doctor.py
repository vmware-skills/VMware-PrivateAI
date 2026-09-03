"""vmware-privateai doctor — the self-check that had no self-check.

Added the same round the doctor was (2026-08-31): 217 lines of diagnostic code
shipped with zero coverage.

Every test here starts from a *fully green* environment and perturbs exactly one
thing, so a red result can only have come from the check the test is named after.
The first draft of this file did the opposite — it left `targets: []` in every
fixture, which fails on its own, so three tests asserted `is False` for a reason
that had nothing to do with their subject: deleting the entire .env permission
check left them green. A test for a diagnostic has to be able to tell *which*
row went red, or it is only asserting that something, somewhere, is wrong.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from vmware_privateai import doctor


class _About:
    version = "8.0.3"
    build = "24022515"


class _SI:
    content = type("Content", (), {"about": _About()})()


def _green(tmp: Path, monkeypatch, *, env: bool = True, mode: int = 0o600, targets: bool = True):
    """A doctor run in which every check passes. Tests break one thing from here."""
    cfg = tmp / "config.yaml"
    cfg.write_text("targets: []\n", encoding="utf-8")
    monkeypatch.setattr(doctor, "CONFIG_FILE", cfg)

    envf = tmp / ".env"
    if env:
        envf.write_text("X=1", encoding="utf-8")
        os.chmod(envf, mode)
    monkeypatch.setattr(doctor, "ENV_FILE", envf)

    target_list = [type("T", (), {"name": "vc-prod"})()] if targets else []
    monkeypatch.setattr(doctor, "load_config", lambda: type("C", (), {"targets": target_list})())

    import vmware_privateai.connection as conn

    class _Mgr:
        def __init__(self, cfg): ...
        def connect(self, name):
            return _SI()
        def disconnect(self, name): ...

    monkeypatch.setattr(conn, "ConnectionManager", _Mgr)
    return conn


def test_a_fully_configured_environment_passes(tmp_path, monkeypatch):
    """The baseline every other test perturbs. If this ever goes red the others
    stop meaning anything, because they would fail for the baseline's reason."""
    _green(tmp_path, monkeypatch)
    assert doctor.run_doctor() is True


def test_a_missing_env_fails(tmp_path, monkeypatch):
    _green(tmp_path, monkeypatch, env=False)
    assert doctor.run_doctor() is False


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits only")
def test_a_world_readable_env_fails(tmp_path, monkeypatch):
    _green(tmp_path, monkeypatch, mode=0o644)
    assert doctor.run_doctor() is False


def test_zero_configured_targets_is_a_failure(tmp_path, monkeypatch):
    """A doctor that reports success on a machine where nothing is configured is
    telling the operator the one thing they cannot act on."""
    _green(tmp_path, monkeypatch, targets=False)
    assert doctor.run_doctor() is False


def test_an_unreachable_target_is_reported_not_raised(tmp_path, monkeypatch):
    """The failure is the answer. A doctor that raises on a dead vCenter tells the
    operator nothing about which of config / credentials / network is wrong."""
    conn = _green(tmp_path, monkeypatch)

    class _Dead:
        def __init__(self, cfg): ...
        def connect(self, name):
            raise ConnectionError("No route to host")
        def disconnect(self, name): ...

    monkeypatch.setattr(conn, "ConnectionManager", _Dead)
    assert doctor.run_doctor() is False
