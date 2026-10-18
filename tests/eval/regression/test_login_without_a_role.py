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
    """SmartConnect logs in, but the account can read nothing.

    Both signals the policy requires: vCenter shows no session for the account,
    and a container view on the root folder is refused with NotAuthenticated.
    """
    monkeypatch.setenv("VMWARE_PRIVATEAI_LAB_PASSWORD", "pw")
    si = MagicMock(name="si")
    si.content.sessionManager.currentSession = None
    si.content.viewManager.CreateContainerView.side_effect = vim.fault.NotAuthenticated()
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


def test_scoped_account_without_a_session_still_connects(monkeypatch):
    """``currentSession`` alone is not a verdict: a readable root view lets it through."""
    monkeypatch.setenv("VMWARE_PRIVATEAI_LAB_PASSWORD", "pw")
    si = MagicMock(name="si")
    si.content.sessionManager.currentSession = None  # the root view below opens
    mgr = ConnectionManager(_config())
    with patch("pyVim.connect.SmartConnect", return_value=si), patch(
        "pyVim.connect.Disconnect"
    ) as dis:
        assert mgr.connect("lab") is si  # no NoVsphereRoleError
        assert mgr.list_connected() == ["lab"]  # cached like any session
        dis.assert_not_called()
        view = si.content.viewManager.CreateContainerView.return_value
        view.Destroy.assert_called_once_with()  # the probe leaves no view behind
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


# 2026-10-18, lab vCenter 8.0.3, SSO account with no permission anywhere:
# vCenter refuses the *login itself* with NoPermission (System.View on the root
# folder). Before the fix that raw fault reached doctor and every tool.
def _login_refused(exc: Exception, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VMWARE_PRIVATEAI_LAB_PASSWORD", "pw")
    mgr = ConnectionManager(_config())
    with patch("pyVim.connect.SmartConnect", side_effect=exc):
        try:
            mgr.connect("lab")
        except Exception as e:  # noqa: BLE001 — the test inspects whatever is raised
            return e
    raise AssertionError("connect did not raise")


def test_login_refused_with_no_permission_names_the_missing_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fault = vim.fault.NoPermission(
        privilegeId="System.View", msg="Permission to perform this operation was denied."
    )
    err = _login_refused(fault, monkeypatch)
    assert isinstance(err, NoVsphereRoleError)
    msg = str(err)
    assert "no vSphere role" in msg and "Read-Only" in msg and "'lab'" in msg
    assert "dynamicType" not in msg and "operation failed" not in msg
    assert len(msg) <= 300
    assert err.__cause__ is fault


def test_wrong_password_is_not_reported_as_a_missing_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    err = _login_refused(vim.fault.InvalidLogin(), monkeypatch)
    assert not isinstance(err, NoVsphereRoleError)
    assert isinstance(err, vim.fault.InvalidLogin)


@pytest.mark.parametrize(
    ("privilege", "esxi"),
    [("System.View", True), ("Sessions.ValidateSession", False), (None, False)],
)
def test_other_login_refusals_get_a_neutral_remedy(
    privilege, esxi, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Review 2026-10-18 (M3): an ESXi host (lockdown mode, host permissions) or
    # another privilege must not be sent to vCenter Global Permissions.
    from dataclasses import replace

    fault = vim.fault.NoPermission(privilegeId=privilege)
    mgr = ConnectionManager(_config())
    if esxi:
        esxi_targets = tuple(replace(t, type="esxi") for t in mgr._config.targets)
        mgr._config = replace(mgr._config, targets=esxi_targets)
    monkeypatch.setenv("VMWARE_LAB_PASSWORD", "pw")
    monkeypatch.setenv("VMWARE_PRIVATEAI_LAB_PASSWORD", "pw")
    with patch("pyVim.connect.SmartConnect", side_effect=fault):
        with pytest.raises(Exception) as info:
            mgr.connect("lab")
    msg = str(info.value)
    assert "Global Permissions" not in msg and "lockdown mode" in msg
    if privilege:
        assert f"({privilege})" in msg
    assert info.value.__cause__ is fault and len(msg) <= 300
