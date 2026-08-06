"""GPU inventory MCP tools (5 read, 1 write) — the "what GPUs exist, who uses them, assign one" surface.

gpu_host_list, gpu_host_get, gpu_device_list, gpu_utilization, gpu_consumer_list [READ];
vgpu_assign [WRITE]. Signatures use ``Optional[X]`` (not PEP 604) because FastMCP/Pydantic
reflect them under interpreters where the union form can raise (踩坑 #33).
"""

from __future__ import annotations

from typing import Optional

from vmware_policy import vmware_tool

from vmware_privateai.mcp_server._shared import _audit, _get_connection, _safe_error, _target_name, mcp

_READ = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}
_WRITE = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True}


@mcp.tool(annotations=_READ)
@vmware_tool(risk_level="low")
def gpu_host_list(
    name: Optional[str] = None,
    vendor: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    target: Optional[str] = None,
) -> dict:
    """[READ] List ESXi hosts that have at least one GPU, filtered by host name / GPU vendor.

    Returns a {items, returned, limit, total, truncated, hint} envelope; each item has
    host, gpu_count, vendors, vgpu_vms (VMs currently backed by a vGPU on that host).
    Use gpu_host_get for full per-GPU detail of one host. Requires a valid vCenter connection.

    Args:
        name: Substring-match the host name.
        vendor: Substring-match the GPU vendor (e.g. "NVIDIA").
        limit: Page size (default 50).
        offset: Page offset.
        target: vCenter/ESXi target from config.yaml; omit to use the default.
    """
    try:
        from vmware_privateai.ops.gpu import list_gpu_hosts

        return list_gpu_hosts(_get_connection(target), name=name, vendor=vendor, limit=limit, offset=offset)
    except Exception as exc:  # noqa: BLE001
        return {"error": _safe_error(exc, "gpu_host_list")}


@mcp.tool(annotations=_READ)
@vmware_tool(risk_level="low")
def gpu_host_get(host_name: str, target: Optional[str] = None) -> dict:
    """[READ] Full GPU detail for one host by name — every GPU's device, type, memory, consumers.

    A wrong host name returns a teaching error listing hosts that do have GPUs, not a
    traceback. Use gpu_host_list to discover host names.

    Args:
        host_name: The ESXi host name (from gpu_host_list).
        target: vCenter/ESXi target from config.yaml; omit to use the default.
    """
    try:
        from vmware_privateai.ops.gpu import get_gpu_host

        return get_gpu_host(_get_connection(target), host_name)
    except Exception as exc:  # noqa: BLE001
        return {"error": _safe_error(exc, "gpu_host_get")}


@mcp.tool(annotations=_READ)
@vmware_tool(risk_level="low")
def gpu_device_list(
    host: Optional[str] = None,
    vendor: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    target: Optional[str] = None,
) -> dict:
    """[READ] List physical GPU devices across all hosts (flattened), filtered by host / vendor.

    Each item has host, device, type, vendor, pci_id, memory_mb, vm_count. Use this to find
    idle GPUs (vm_count 0) or to inventory a model across the estate. Paginated envelope.

    Args:
        host: Substring-match the host name.
        vendor: Substring-match the GPU vendor.
        limit: Page size (default 50).
        offset: Page offset.
        target: vCenter/ESXi target from config.yaml; omit to use the default.
    """
    try:
        from vmware_privateai.ops.gpu import list_gpu_devices

        return list_gpu_devices(_get_connection(target), host=host, vendor=vendor, limit=limit, offset=offset)
    except Exception as exc:  # noqa: BLE001
        return {"error": _safe_error(exc, "gpu_device_list")}


@mcp.tool(annotations=_READ)
@vmware_tool(risk_level="low")
def gpu_utilization(
    vm: Optional[str] = None,
    top: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
    target: Optional[str] = None,
) -> dict:
    """[READ] Real-time GPU utilization per vGPU VM (busiest first) — gpu_pct, mem_pct, temp_c.

    Each item has vm, profile, gpu_pct, mem_pct, mem_used_kb, temp_c, metrics_available, idle.
    Requires the NVIDIA host GPU driver — a VM with no samples shows metrics_available=false
    (not an error). Deep per-SM/MIG-slice metrics are not available via vSphere (need DCGM).

    Args:
        vm: Substring-match the VM name.
        top: Keep only the N busiest VMs by GPU utilization.
        limit: Page size (default 50).
        offset: Page offset.
        target: vCenter/ESXi target from config.yaml; omit to use the default.
    """
    try:
        from vmware_privateai.ops.utilization import gpu_utilization as _util

        return _util(_get_connection(target), vm=vm, top=top, limit=limit, offset=offset)
    except Exception as exc:  # noqa: BLE001
        return {"error": _safe_error(exc, "gpu_utilization")}


@mcp.tool(annotations=_READ)
@vmware_tool(risk_level="low")
def gpu_consumer_list(
    profile: Optional[str] = None,
    vm: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    target: Optional[str] = None,
) -> dict:
    """[READ] List VMs consuming a vGPU and the profile each holds (e.g. "grid_a100-4c").

    Each item has vm and profile. This is the "who holds the GPU" view — pair it with
    vmware-aiops to power a VM off, or with a future vgpu assignment tool. Paginated.

    Args:
        profile: Substring-match the vGPU profile name.
        vm: Substring-match the VM name.
        limit: Page size (default 50).
        offset: Page offset.
        target: vCenter/ESXi target from config.yaml; omit to use the default.
    """
    try:
        from vmware_privateai.ops.gpu import list_gpu_consumers

        return list_gpu_consumers(_get_connection(target), profile=profile, vm=vm, limit=limit, offset=offset)
    except Exception as exc:  # noqa: BLE001
        return {"error": _safe_error(exc, "gpu_consumer_list")}


@mcp.tool(annotations=_WRITE)
@vmware_tool(risk_level="high")
def vgpu_assign(
    vm_name: str,
    profile: str,
    confirm: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Set a VM's vGPU profile (e.g. "grid_a100-4c"). Requires the VM to be powered OFF.

    confirm=False (default) previews the blast radius — current profile, target profile, power
    state, and that a power-off is required — without acting. confirm=True applies it, but refuses
    a running VM with a teaching error (power it off via vmware-aiops first). This tool never powers
    the VM off itself. Audited.

    Args:
        vm_name: The VM to reconfigure (from gpu_consumer_list).
        profile: The target vGPU profile name (from gpu_host_get).
        confirm: False previews; True applies (VM must be powered off).
        target: vCenter/ESXi target from config.yaml; omit to use the default.
    """
    try:
        from vmware_privateai.ops.assign import assign_vgpu

        return assign_vgpu(
            _get_connection(target), vm_name, profile,
            confirm=confirm, audit_logger=_audit, target_name=_target_name(target),
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": _safe_error(exc, "vgpu_assign")}
