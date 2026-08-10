"""vGPU profile-change validation (read) — the pre-flight for vgpu_assign.

The most-escalated vGPU field case is "customer is trying to change the GPU profile on a VM"
(SR 36933109). Changing a vGPU profile fails at ReconfigVM time for two avoidable reasons: the
VM is powered on, or the VM's host does not actually offer the requested profile. This op checks
BOTH up front — read-only, no reconfigure — so the operator sees exactly what would block the
change before touching the (write, high-risk) vgpu_assign tool.

All facts come from VERIFIED pyVmomi paths already used by the assign/profile ops:
VM power state + current vGPU backing, and the VM host's QueryConfigTarget profile catalog.
"""

from __future__ import annotations

from typing import Any

from pyVmomi import vim

from vmware_privateai.connection import get_content
from vmware_privateai.ops._errors import GpuNotFoundError
from vmware_privateai.ops._sanitize import _sanitize
from vmware_privateai.ops.assign import _current_profile
from vmware_privateai.ops.gpu import _retrieve_props
from vmware_privateai.ops.vgpu_profiles import _project_profile, _query_config_target, _vgpu_infos

# Spec keys this module touches — asserted ⊆ PYVMOMI_OBJECTS by the gate (踩坑 #36).
SPEC_KEYS_USED: frozenset[str] = frozenset(
    {"vm_vgpu_consumer", "vm_power_state", "vm_runtime_host", "vgpu_profile_catalog"}
)


def _find_vm_with_host(si: Any, vm_name: str) -> dict:
    """Locate a VM by exact name, fetching the fields the validation needs in one batch."""
    content = get_content(si)
    rows = _retrieve_props(
        content, vim.VirtualMachine, ["name", "config.hardware.device", "runtime.powerState", "runtime.host"]
    )
    for r in rows:
        if r.get("name") == vm_name:
            return r
    known = sorted(r.get("name", "") for r in rows)[:10]
    hint = f" Known VMs: {', '.join(known)}." if known else ""
    raise GpuNotFoundError(f"VM '{vm_name}' not found on this target.{hint} Run gpu_consumer_list to see vGPU VMs.")


def validate_vgpu_change(si: Any, vm_name: str, target_profile: str) -> dict:
    """Check whether setting ``vm_name``'s vGPU profile to ``target_profile`` would succeed.

    Returns a read-only verdict: ``can_apply`` plus ``blocking_reasons``, the current→target
    profiles, the VM power state, and whether the VM's host offers the target profile (with its
    framebuffer). Does NOT reconfigure anything — run vgpu_assign (confirm=true) to apply once
    ``can_apply`` is true.
    """
    if not target_profile:
        raise GpuNotFoundError(
            "A target vGPU profile is required (e.g. 'nvidia_a100-4c'). Run gpu_host_get or "
            "vgpu_profile_list to see the profiles a host offers."
        )

    row = _find_vm_with_host(si, vm_name)
    devices = row.get("config.hardware.device") or []
    current = _current_profile(devices)
    power_state = str(row.get("runtime.powerState") or "")
    running = power_state == "poweredOn"

    # The VM's OWN host is what ReconfigVM places against. runtime.host can be absent (orphaned VM,
    # or a null host on some powered-off states) — treat "unknown host" as unknown, never assert the
    # host lacks the profile (review M1, 踩坑 形态 #1). Reading host.name and QueryConfigTarget are BOTH
    # per-object RPCs, so they share one guard — a mid-call ManagedObjectNotFound / transport fault
    # must surface as a teaching "host unreachable", not _safe_error's opaque mask (review M2, 踩坑 #37).
    host_mo = row.get("runtime.host")
    host_name = ""
    offered: list[dict] = []
    host_unknown = host_mo is None
    profile_query_failed = False
    if host_mo is not None:
        try:
            host_name = _sanitize(getattr(host_mo, "name", "") or "")
            offered = [_project_profile(vp) for vp in _vgpu_infos(_query_config_target(host_mo))]
        except Exception:  # noqa: BLE001 — a per-host RPC fault → unknown, not a crash
            profile_query_failed = True

    match = next((p for p in offered if p["profile"] == target_profile), None)
    host_offers = match is not None
    host_determined = not host_unknown and not profile_query_failed

    reasons: list[str] = []
    if running:
        reasons.append("VM is powered on — a vGPU change needs it powered off (power off via vmware-aiops first)")
    if host_unknown:
        reasons.append("could not determine the VM's host (VM may be orphaned / not registered) — re-check the VM")
    elif profile_query_failed:
        reasons.append(
            f"could not query host '{host_name or '?'}' profile catalog (host disconnected / not responding)"
        )
    elif not host_offers:
        near = ", ".join(sorted(p["profile"] for p in offered)[:8]) or "(none)"
        reasons.append(f"host '{host_name}' does not offer profile '{target_profile}'. Profiles it offers: {near}")

    can_apply = not reasons
    return {
        "vm": _sanitize(vm_name),
        "host": host_name or None,
        "current_profile": _sanitize(current) or None,
        "target_profile": _sanitize(target_profile),
        "power_state": power_state,
        "host_offers_target": host_offers if host_determined else None,
        "target_framebuffer_gib": match["framebuffer_gib"] if match else None,
        "can_apply": can_apply,
        "blocking_reasons": reasons,
        "hint": (
            f"Ready — run vgpu_assign('{vm_name}', '{target_profile}', confirm=true) to apply."
            if can_apply
            else "Resolve blocking_reasons, then re-validate before vgpu_assign."
        ),
    }
