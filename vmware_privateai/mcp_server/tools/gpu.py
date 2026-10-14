"""GPU inventory MCP tools (5 read, 1 write) — the "what GPUs exist, who uses them, assign one" surface.

list_gpu_hosts, get_gpu_host, list_gpu_devices, get_gpu_utilization, list_gpu_consumers [READ];
assign_vgpu [WRITE]. Signatures use ``Optional[X]`` (not PEP 604) because FastMCP/Pydantic
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
def list_gpu_hosts(
    name: Optional[str] = None,
    vendor: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    target: Optional[str] = None,
) -> dict:
    """[READ] List ESXi hosts that have at least one GPU, filtered by host name / GPU vendor.

    Returns a {items, returned, limit, total, truncated, hint} envelope; each item has
    host, gpu_count, vendors, vgpu_vms (VMs currently backed by a vGPU on that host).
    Use get_gpu_host for full per-GPU detail of one host. Requires a valid vCenter connection.

    Args:
        name: Substring-match the host name.
        vendor: Substring-match the GPU vendor (e.g. "NVIDIA").
        limit: Page size (default 50).
        offset: Page offset.
        target: vCenter/ESXi target from config.yaml; omit to use the default.
    """
    try:
        from vmware_privateai.ops.gpu import list_gpu_hosts as _ops_list_gpu_hosts

        return _ops_list_gpu_hosts(_get_connection(target), name=name, vendor=vendor, limit=limit, offset=offset)
    except Exception as exc:  # noqa: BLE001
        return {"error": _safe_error(exc, "list_gpu_hosts")}


@mcp.tool(annotations=_READ)
@vmware_tool(risk_level="low")
def get_gpu_host(host_name: str, target: Optional[str] = None) -> dict:
    """[READ] Full GPU detail for one host by name — every GPU's device, type, memory, consumers.

    A wrong host name returns a teaching error listing hosts that do have GPUs, not a
    traceback. Use list_gpu_hosts to discover host names.

    Args:
        host_name: The ESXi host name (from list_gpu_hosts).
        target: vCenter/ESXi target from config.yaml; omit to use the default.
    """
    try:
        from vmware_privateai.ops.gpu import get_gpu_host as _ops_get_gpu_host

        return _ops_get_gpu_host(_get_connection(target), host_name)
    except Exception as exc:  # noqa: BLE001
        return {"error": _safe_error(exc, "get_gpu_host")}


@mcp.tool(annotations=_READ)
@vmware_tool(risk_level="low")
def list_gpu_devices(
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
        from vmware_privateai.ops.gpu import list_gpu_devices as _ops_list_gpu_devices

        return _ops_list_gpu_devices(_get_connection(target), host=host, vendor=vendor, limit=limit, offset=offset)
    except Exception as exc:  # noqa: BLE001
        return {"error": _safe_error(exc, "list_gpu_devices")}


@mcp.tool(annotations=_READ)
@vmware_tool(risk_level="low")
def get_gpu_utilization(
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
        from vmware_privateai.ops.utilization import gpu_utilization as _ops_gpu_utilization

        return _ops_gpu_utilization(_get_connection(target), vm=vm, top=top, limit=limit, offset=offset)
    except Exception as exc:  # noqa: BLE001
        return {"error": _safe_error(exc, "get_gpu_utilization")}


@mcp.tool(annotations=_READ)
@vmware_tool(risk_level="low")
def list_gpu_consumers(
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
        from vmware_privateai.ops.gpu import list_gpu_consumers as _ops_list_gpu_consumers

        return _ops_list_gpu_consumers(_get_connection(target), profile=profile, vm=vm, limit=limit, offset=offset)
    except Exception as exc:  # noqa: BLE001
        return {"error": _safe_error(exc, "list_gpu_consumers")}


@mcp.tool(annotations=_READ)
@vmware_tool(risk_level="low")
def check_gpu_host_readiness(
    host: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    target: Optional[str] = None,
) -> dict:
    """[READ] Report whether each GPU host is actually configured to serve vGPU / PAIS.

    Each item has host, vgpu_ready (verdict), gpu_count, vendors, total_gpu_memory_mb,
    default_graphics_type (must be 'sharedDirect' for vGPU), vgpu_profiles_offered,
    active_vgpu_vms, and blocking_reasons explaining any "not ready". The NVIDIA driver / MFT
    VIB version and MIG geometry are NOT in the vSphere API — every item carries a driver_note
    pointing to nvidia-smi / esxcli. Only hosts that have a GPU are returned. Paginated.

    Args:
        host: Substring-match the host name (scopes the per-host profile query).
        limit: Page size (default 50).
        offset: Page offset.
        target: vCenter/ESXi target from config.yaml; omit to use the default.
    """
    try:
        from vmware_privateai.ops.readiness import gpu_host_readiness as _ops_gpu_host_readiness

        return _ops_gpu_host_readiness(_get_connection(target), host=host, limit=limit, offset=offset)
    except Exception as exc:  # noqa: BLE001
        return {"error": _safe_error(exc, "check_gpu_host_readiness")}


@mcp.tool(annotations=_WRITE)
@vmware_tool(risk_level="high")
def assign_vgpu(
    vm_name: str,
    profile: str,
    confirm: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Set a VM's vGPU profile (e.g. "grid_a100-4c"). Requires the VM to be powered OFF.

    A bare call returns blast_radius — the VM's name and id, current and target profile, whether
    its vGPU device is edited or a new one added, other passthrough devices left untouched, power
    state, blockers, unmeasured — and changes nothing. confirm=True applies it, but refuses a
    running or suspended VM (power it off via vmware-aiops first), a name shared by several VMs,
    and a VM whose power state or devices cannot be read. This tool never powers the VM off itself.
    Show blast_radius to the user and wait for their decision. Do not set confirm=True on your own
    because the user asked for this earlier: they have not seen the blast radius yet. Audited.

    Args:
        vm_name: The VM to reconfigure (from list_gpu_consumers).
        profile: The target vGPU profile name (from get_gpu_host).
        confirm: False (default) returns the blast radius and changes nothing. True applies it
            (the VM must be powered off).
        target: vCenter/ESXi target from config.yaml; omit to use the default.
    """
    try:
        from vmware_privateai.ops.assign import assign_vgpu as _ops_assign_vgpu

        return _ops_assign_vgpu(
            _get_connection(target),
            vm_name,
            profile,
            confirm=confirm,
            audit_logger=_audit,
            target_name=_target_name(target),
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": _safe_error(exc, "assign_vgpu")}
