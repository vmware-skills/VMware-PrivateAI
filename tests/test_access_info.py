"""HLD §17 D-17: ``get_access_info`` and the per-tool privilege table."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pyVmomi import VmomiSupport
from typer.testing import CliRunner

from vmware_privateai.access_requirements import REQUIREMENTS
from vmware_privateai.config import AppConfig, TargetConfig
from vmware_privateai.connection import ConnectionManager
from vmware_privateai.mcp_server import server
from vmware_privateai.mcp_server.tools.access import get_access_info


def _known_privileges() -> set[str]:
    privs: set[str] = set()
    for name in list(VmomiSupport._managedDefMap):
        try:
            t = VmomiSupport.GetVmodlType(name)
        except Exception:
            continue
        privs.update(m.privId for m in getattr(t, "_methodInfo", {}).values() if m.privId)
        privs.update(p.privId for p in t._GetPropertyList() if getattr(p, "privId", None))
    return privs


def _fake_si(granted: dict[str, bool]) -> MagicMock:
    si = MagicMock()
    si.content.sessionManager.currentSession = SimpleNamespace(key="sess-1", userName="VSPHERE.LOCAL\\svc-privateai")

    def has_privilege(entity, sessionId, privId):  # noqa: N803 — vSphere's names
        avail = [SimpleNamespace(privId=p, isGranted=granted.get(p, False)) for p in privId]
        return [SimpleNamespace(privAvailability=avail)]

    si.content.authorizationManager.HasPrivilegeOnEntities.side_effect = has_privilege
    return si


@pytest.fixture
def manager(monkeypatch: pytest.MonkeyPatch) -> ConnectionManager:
    cfg = AppConfig(targets=(TargetConfig(name="vc1", host="vc1.example.com", config_username="u"),))
    mgr = ConnectionManager(cfg)
    monkeypatch.setattr("vmware_privateai.mcp_server.tools.access._get_manager", lambda: mgr)
    return mgr


def test_requirements_cover_exactly_the_registered_tools() -> None:
    tools = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert set(REQUIREMENTS) == tools


def test_every_privilege_is_one_vsphere_defines() -> None:
    known = _known_privileges()
    assert len(known) > 100, "pyVmomi metadata probe found almost nothing"
    unknown = {t: [p for p in v if p not in known] for t, v in REQUIREMENTS.items()}
    assert not {t: v for t, v in unknown.items() if v}


def test_get_access_info_is_registered_read_only() -> None:
    tool = next(t for t in asyncio.run(server.mcp.list_tools()) if t.name == "get_access_info")
    assert tool.annotations.readOnlyHint is True
    assert tool.description.startswith("[READ]")


def test_tool_reports_the_helpers_verdict(manager: ConnectionManager) -> None:
    si = _fake_si({"System.View": True, "System.Read": False})
    with patch.object(manager, "connect", return_value=si) as connect:
        out = get_access_info(tool="assign_vgpu")
    connect.assert_called_once_with("vc1")
    assert out["target"] == "vc1"
    assert out["account"] == "VSPHERE.LOCAL\\svc-privateai"
    assert out["tool"]["verdict"] == "not_granted_at_root"
    assert out["tool"]["missing_at_root"] == ["System.Read"]


def test_whole_server_summary_groups_tools(manager: ConnectionManager) -> None:
    si = _fake_si({"System.View": True, "System.Read": True})
    with patch.object(manager, "connect", return_value=si):
        out = get_access_info()
    assert "list_pais_models" in out["tools_no_privilege_needed"]
    assert "assign_vgpu" in out["tools_granted_at_root"]
    assert out["tools_not_granted_at_root"] == []


def test_unknown_tool_is_a_teaching_error(manager: ConnectionManager) -> None:
    with patch.object(manager, "connect", return_value=_fake_si({})):
        out = get_access_info(tool="assign_vgpo")
    assert "error" in out
    assert "assign_vgpu" in out["error"]  # "Did you mean ..."


def test_cli_access_info_prints_the_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    from vmware_privateai import cli

    cfg = AppConfig(targets=(TargetConfig(name="vc1", host="vc1.example.com", config_username="u"),))
    monkeypatch.setattr("vmware_privateai.connection.load_config", lambda path=None: cfg)
    si = _fake_si({"System.View": True, "System.Read": True})
    with patch.object(ConnectionManager, "connect", return_value=si):
        result = CliRunner().invoke(cli.app, ["access-info", "--tool", "list_gpu_hosts"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out["tool"]["verdict"] == "granted_at_root"
