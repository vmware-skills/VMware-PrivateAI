"""Access self-diagnosis MCP tool (1 read, HLD §17 D-17) — "what may this account do".

get_access_info [READ]. Signature uses ``Optional[X]`` (not PEP 604) because FastMCP/Pydantic
reflect it at import time (踩坑 #33).
"""

from __future__ import annotations

from typing import Optional

from vmware_policy import vmware_tool
from vmware_policy.access import unknown_tool_message, vsphere_access_info

from vmware_privateai.access_requirements import REQUIREMENTS
from vmware_privateai.mcp_server._shared import _get_manager, _safe_error, mcp

_READ = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}


@mcp.tool(annotations=_READ)
@vmware_tool(risk_level="low")
def get_access_info(tool: Optional[str] = None, target: Optional[str] = None) -> dict:
    """[READ] Which account this server uses, and whether it holds each tool's vSphere privileges.

    Use this when a tool fails with NoPermission, or before assign_vgpu, to check what the
    configured account may do. Asks vCenter/ESXi about the current session (needs only
    System.View, so any account can ask about itself). Returns target, account, the privilege
    states, and for one tool its requires/verdict/meaning; without tool, every tool grouped by
    verdict. Gotchas: privileges are checked on the root folder only, and not_granted_at_root
    is NOT "no access" — it may be granted on a specific cluster, host or VM. undetermined means
    vCenter did not answer; never read it as granted. PAIS tools use a bearer token, not vSphere,
    so they show as needing no vSphere privilege; assign_vgpu's entry includes the
    device-change privilege (VirtualMachine.Config.AddRemoveDevice).

    Args:
        tool: One tool name on this server, e.g. "assign_vgpu". Omit to summarize every tool.
        target: vCenter/ESXi target from config.yaml; omit to use the default.
    """
    unknown = unknown_tool_message(tool, REQUIREMENTS)  # no login for a misspelled name
    if unknown:
        return {"error": unknown}
    try:
        mgr = _get_manager()
        name = mgr.resolve_target_name(target)
        si = mgr.connect(name)
        return vsphere_access_info(si, target=name, requirements=REQUIREMENTS, tool=tool)
    except Exception as exc:  # noqa: BLE001
        return {"error": _safe_error(exc, "get_access_info")}
