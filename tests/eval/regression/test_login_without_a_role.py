"""A login that works but has no vSphere role (AIops#47).

Reported on vCenter 8.0.3: an account with no vSphere role logs in, vCenter
shows no session for it (``currentSession`` is ``None``), and every inventory
call then fails with ``vim.fault.NotAuthenticated``. ``doctor`` passed, and the
error told the user nothing about roles.

Driven through the real ConnectionManager, doctor, CLI handler and MCP tool;
only ``SmartConnect`` / ``Disconnect`` are fakes, so nothing touches a network.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest
import typer
from pyVmomi import vim
from rich.console import Console

from vmware_privateai import doctor
from vmware_privateai.config import AppConfig, TargetConfig
from vmware_privateai.connection import _SI_ATEXIT, ConnectionManager, NoVsphereRoleError
from vmware_privateai.mcp_server import _shared
from vmware_privateai.mcp_server.tools import gpu as gpu_tools

ROLE_WORDS = ("no vSphere role", "Read-Only")


def _config() -> AppConfig:
    return AppConfig(
        targets=(
            TargetConfig(name="lab", host="vc.example.com", config_username="u@vsphere.local"),
        )
    )


@pytest.fixture
def role_less(monkeypatch):
    """SmartConnect logs in, but vCenter shows no session for the account."""
    monkeypatch.setenv("VMWARE_PRIVATEAI_LAB_PASSWORD", "pw")
    si = MagicMock(name="si")
    si.content.sessionManager.currentSession = None
    with (
        patch("pyVim.connect.SmartConnect", return_value=si) as smart,
        patch("pyVim.connect.Disconnect") as dis,
    ):
        yield si, smart, dis


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_role_less_login_is_refused_closed_and_not_cached(role_less):
    si, smart, dis = role_less
    mgr = ConnectionManager(_config())
    with pytest.raises(NoVsphereRoleError) as excinfo:
        mgr.connect("lab")
    for word in ROLE_WORDS:
        assert word in str(excinfo.value)
    dis.assert_called_once_with(si)
    assert id(si) not in _SI_ATEXIT
    assert mgr.list_connected() == []
    with pytest.raises(NoVsphereRoleError):
        mgr.connect("lab")
    assert smart.call_count == 2


def test_normal_login_is_unaffected(monkeypatch):
    monkeypatch.setenv("VMWARE_PRIVATEAI_LAB_PASSWORD", "pw")
    si = MagicMock(name="si")
    si.content.sessionManager.currentSession = MagicMock()
    mgr = ConnectionManager(_config())
    with patch("pyVim.connect.SmartConnect", return_value=si), patch(
        "pyVim.connect.Disconnect"
    ) as dis:
        assert mgr.connect("lab") is si
        assert mgr.list_connected() == ["lab"]
        dis.assert_not_called()
        mgr.disconnect_all()


def test_doctor_row_names_the_missing_role(role_less, monkeypatch, tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("targets: []\n", encoding="utf-8")
    monkeypatch.setattr(doctor, "resolve_config_path", lambda *a, **k: cfg_file)
    monkeypatch.setattr(doctor, "load_config", lambda *a, **k: _config())
    buf = io.StringIO()
    monkeypatch.setattr(doctor, "console", Console(file=buf, width=1000))
    assert doctor.run_doctor() is False
    row = next(line for line in buf.getvalue().splitlines() if "vCenter 'lab' reachable" in line)
    assert "FAIL" in row
    for word in ROLE_WORDS:
        assert word in row


def test_mcp_tool_returns_the_role_message(role_less, monkeypatch):
    monkeypatch.setattr(_shared, "_manager", ConnectionManager(_config()))
    out = gpu_tools.list_gpu_hosts(target="lab")
    for word in ROLE_WORDS:
        assert word in out["error"]


def test_cli_handler_prints_the_role_message(role_less, monkeypatch, capsys):
    from vmware_privateai.cli import _common

    monkeypatch.setattr("vmware_privateai.connection.load_config", lambda *a, **k: _config())

    @_common.cli_errors
    def run():
        _common._get_connection("lab")

    with pytest.raises(typer.Exit):
        run()
    out = _flat(capsys.readouterr().out)
    for word in ROLE_WORDS:
        assert word in out


# ── NotAuthenticated raised later: expired session, or a role removed ─────────


def _says_both(text: str) -> None:
    text = _flat(text)
    assert "expired" in text and "run the command again" in text
    assert "no vSphere role" in text and "Read-Only" in text


def test_cli_not_authenticated_says_both_causes(capsys):
    from vmware_privateai.cli._common import cli_errors

    @cli_errors
    def boom():
        raise vim.fault.NotAuthenticated()

    with pytest.raises(typer.Exit):
        boom()
    _says_both(capsys.readouterr().out)


def test_cli_other_faults_still_propagate():
    """The new branch must not swallow every exception."""
    from vmware_privateai.cli._common import cli_errors

    @cli_errors
    def boom():
        raise KeyError("unplanned")

    with pytest.raises(KeyError):
        boom()


def test_mcp_not_authenticated_says_both_causes(monkeypatch):
    def _boom(target=None):
        raise vim.fault.NotAuthenticated()

    monkeypatch.setattr(gpu_tools, "_get_connection", _boom)
    out = gpu_tools.list_gpu_hosts(target="lab")
    _says_both(out["error"])
    assert "unexpected error" not in out["error"]
