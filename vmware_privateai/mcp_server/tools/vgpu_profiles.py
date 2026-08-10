"""vGPU / DirectPath profile catalog MCP tools (2 read) — "what profiles can I assign".

vgpu_profile_list, directpath_profile_list [READ]. Signatures use ``Optional[X]`` (not PEP
604) because FastMCP/Pydantic reflect them at import time (踩坑 #33).
"""

from __future__ import annotations

from typing import Optional

from vmware_policy import vmware_tool

from vmware_privateai.mcp_server._shared import _get_connection, _safe_error, mcp

_READ = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}


@mcp.tool(annotations=_READ)
@vmware_tool(risk_level="low")
def vgpu_profile_list(
    host: Optional[str] = None,
    model: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    target: Optional[str] = None,
) -> dict:
    """[READ] List the available vGPU profile catalog (what profiles a host can hand a VM).

    Returns a {items, returned, limit, total, truncated, hint} envelope; each item is a distinct
    profile with framebuffer_gib, profile_class, sharing, vendor_id, and the hosts that offer it
    (hosts / host_count). Pair with vgpu_assign to set a VM's profile. Scope to one host with
    `host` — this polls QueryConfigTarget per host. Requires a valid vCenter connection.

    Args:
        host: Substring-match the host name (also scopes the per-host polling).
        model: Substring-match the profile / model name (e.g. "a100").
        limit: Page size (default 50).
        offset: Page offset.
        target: vCenter/ESXi target from config.yaml; omit to use the default.
    """
    try:
        from vmware_privateai.ops.vgpu_profiles import list_vgpu_profiles

        return list_vgpu_profiles(_get_connection(target), host=host, model=model, limit=limit, offset=offset)
    except Exception as exc:  # noqa: BLE001
        return {"error": _safe_error(exc, "vgpu_profile_list")}


@mcp.tool(annotations=_READ)
@vmware_tool(risk_level="low")
def directpath_profile_list(
    name: Optional[str] = None,
    vendor: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    target: Optional[str] = None,
) -> dict:
    """[READ] List vCenter-level DirectPath (dynamic passthrough) profiles. vSphere 9.0+.

    Each item has id, name, vendor, description. On a vCenter older than 9.0 (no
    DirectPathProfileManager) this returns a teaching error routing you to vgpu_profile_list,
    rather than an empty list that would read as "no profiles". Paginated envelope.

    Args:
        name: Substring-match the profile name.
        vendor: Substring-match the vendor name.
        limit: Page size (default 50).
        offset: Page offset.
        target: vCenter target from config.yaml; omit to use the default.
    """
    try:
        from vmware_privateai.ops.vgpu_profiles import list_directpath_profiles

        return list_directpath_profiles(_get_connection(target), name=name, vendor=vendor, limit=limit, offset=offset)
    except Exception as exc:  # noqa: BLE001
        return {"error": _safe_error(exc, "directpath_profile_list")}


@mcp.tool(annotations=_READ)
@vmware_tool(risk_level="low")
def vgpu_profile_validate(
    vm_name: str,
    target_profile: str,
    target: Optional[str] = None,
) -> dict:
    """[READ] Pre-flight a vGPU profile change: would setting vm_name to target_profile succeed?

    Read-only companion to vgpu_assign (no reconfigure). Returns can_apply plus blocking_reasons,
    the current→target profile, power_state (must be off), and whether the VM's OWN host offers
    the target profile (with its framebuffer). Use this before vgpu_assign to see exactly what
    would block the change — the two ReconfigVM failure modes are "VM powered on" and "host does
    not offer this profile".

    Args:
        vm_name: The VM whose vGPU profile would change (from gpu_consumer_list).
        target_profile: The profile you intend to set (from vgpu_profile_list / gpu_host_get).
        target: vCenter/ESXi target from config.yaml; omit to use the default.
    """
    try:
        from vmware_privateai.ops.validate import validate_vgpu_change

        return validate_vgpu_change(_get_connection(target), vm_name, target_profile)
    except Exception as exc:  # noqa: BLE001
        return {"error": _safe_error(exc, "vgpu_profile_validate")}
