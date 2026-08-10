"""GPU host readiness (read) — is this ESXi host actually configured to run vGPU / PAIS?

A recurring field question ("customer's GPU host isn't offering vGPU / MFT VIB / which host is
ready") maps to a few VERIFIED host facts that, together, say whether a host can serve vGPU:
  - it has an accelerated GPU                     (HostSystem.config.graphicsInfo)
  - its default graphics type is 'sharedDirect'   (HostSystem.config.graphicsConfig) — i.e. vGPU
    mode, not vSGA ('shared') and not bare passthrough
  - it actually offers ≥1 vGPU profile            (EnvironmentBrowser.QueryConfigTarget)

What vSphere CANNOT tell us is listed honestly, never invented (踩坑 #36 / spec NO_API): the
NVIDIA host driver / MFT VIB version and MIG geometry are NOT in the vSphere API — they need
``nvidia-smi`` / ``esxcli software vib list`` on the host. This op says so rather than guessing.
"""

from __future__ import annotations

from typing import Any

from pyVmomi import vim

from vmware_privateai.connection import get_content
from vmware_privateai.ops._paging import envelope
from vmware_privateai.ops._sanitize import _sanitize
from vmware_privateai.ops.gpu import _gpus_of, _matches, _retrieve_props
from vmware_privateai.ops.vgpu_profiles import _query_config_target, _vgpu_infos

# Spec keys this module touches — asserted ⊆ PYVMOMI_OBJECTS by the gate (踩坑 #36).
SPEC_KEYS_USED: frozenset[str] = frozenset(
    {"gpu_host_graphics_info", "gpu_host_graphics_config", "vgpu_profile_catalog"}
)

# The host graphics type that means vGPU (time-sliced sharing), as opposed to vSGA ("shared").
_VGPU_GRAPHICS_TYPE = "sharedDirect"

# Honest statement of what the vSphere API does NOT expose (spec NO_API) — surfaced in every result
# so an operator never reads "driver: (absent)" as "driver missing".
_DRIVER_NOTE = (
    "NVIDIA driver / MFT VIB version and MIG geometry are NOT in the vSphere API — verify on the "
    "host with 'nvidia-smi' and 'esxcli software vib list | grep -i nvd'."
)


def _readiness_of(host_props: dict, config_target: Any, *, query_failed: bool = False) -> dict:
    """Assemble one host's GPU readiness from its graphics info, graphics config, and profile catalog."""
    host_name = _sanitize(host_props.get("name", "") or "")
    gpus = _gpus_of(host_props)
    graphics_config = host_props.get("config.graphicsConfig")
    default_type = getattr(graphics_config, "hostDefaultGraphicsType", "") or ""
    assignment_policy = getattr(graphics_config, "sharedPassthruAssignmentPolicy", "") or ""

    profiles_offered = len(_vgpu_infos(config_target)) if config_target is not None else 0

    reasons: list[str] = []
    if not gpus:
        reasons.append("no accelerated GPU found on this host")
    if default_type and default_type != _VGPU_GRAPHICS_TYPE:
        # default_type is a host-authored string reaching the agent — sanitize it here too (review L2).
        reasons.append(f"host default graphics type is '{_sanitize(default_type)}', not 'sharedDirect' (vGPU) mode")
    elif not default_type:
        reasons.append("host default graphics type is not set")
    if query_failed:
        # Unreachable ≠ driverless — do NOT claim "no profiles" when we simply could not ask (review L1).
        reasons.append("vGPU profile query failed (host unreachable — see unreachable_hosts, not a driver verdict)")
    elif profiles_offered == 0:
        reasons.append("host offers no vGPU profiles (driver may be missing — see driver_note)")

    vgpu_ready = bool(gpus) and default_type == _VGPU_GRAPHICS_TYPE and profiles_offered > 0 and not query_failed
    return {
        "host": host_name,
        "vgpu_ready": vgpu_ready,
        "gpu_count": len(gpus),
        "gpu_devices": [g["device"] for g in gpus],
        "vendors": sorted({g["vendor"] for g in gpus if g["vendor"]}),
        "total_gpu_memory_mb": sum(g["memory_mb"] for g in gpus),
        "default_graphics_type": _sanitize(default_type),
        "shared_passthru_assignment_policy": _sanitize(assignment_policy),
        "vgpu_profiles_offered": profiles_offered,
        "active_vgpu_vms": sum(g["vm_count"] for g in gpus),
        "blocking_reasons": reasons,
        "driver_note": _DRIVER_NOTE,
    }


def gpu_host_readiness(
    si: Any,
    *,
    host: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Report per-host vGPU/PAIS readiness across the estate (or one host). Paginated.

    Combines graphics info + graphics config + the per-host vGPU profile catalog into a
    ``vgpu_ready`` verdict with ``blocking_reasons``. Hosts whose per-host QueryConfigTarget
    RPC fails (disconnected / not responding) are listed in ``unreachable_hosts`` rather than
    sinking the whole call (踩坑 形态 #1). Only hosts that actually have a GPU are returned.
    """
    content = get_content(si)
    rows = _retrieve_props(content, vim.HostSystem, ["name", "config.graphicsInfo", "config.graphicsConfig"])
    items: list[dict] = []
    unreachable: list[str] = []
    for r in rows:
        host_name = _sanitize(r.get("name", "") or "")
        if not _matches(host_name, host):
            continue
        if not _gpus_of(r):
            continue  # not a GPU host — out of scope for a GPU readiness report
        try:
            config_target = _query_config_target(r.get("_obj"))
        except Exception:  # noqa: BLE001 — a per-host RPC fault degrades to "unreachable", not a crash
            unreachable.append(host_name)
            items.append({**_readiness_of(r, None, query_failed=True), "profile_query_failed": True})
            continue
        items.append(_readiness_of(r, config_target))
    items.sort(key=lambda h: (not h["vgpu_ready"], h["host"]))
    result = envelope(items, limit=limit, offset=offset)
    result["unreachable_hosts"] = sorted(set(unreachable))
    return result
