"""vGPU assignment (write) — set or replace a VM's vGPU profile via ReconfigVM.

Assigning/changing a vGPU requires the VM to be POWERED OFF (no hot-add, no vMotion
for passthrough — VERIFIED, tests/eval/spec vm_vgpu_assign). This op previews the blast
radius, REFUSES to reconfigure a running VM (routing the operator to power it off via
vmware-aiops), and audits every applied change. Never powers a VM off itself — that is
vmware-aiops's job, kept separate so this tool's blast radius stays "one VM, when off".
"""

from __future__ import annotations

from typing import Any

from pyVmomi import vim

from vmware_privateai.connection import get_content
from vmware_privateai.ops._errors import GpuNotFoundError, PrivateAiError
from vmware_privateai.ops._sanitize import _sanitize
from vmware_privateai.ops.gpu import _retrieve_props, _vgpu_backing

SPEC_KEYS_USED: frozenset[str] = frozenset({"vm_vgpu_consumer", "vm_vgpu_assign"})


def _wait_for_task(task: Any) -> None:
    """Block until a vCenter task finishes; raise its fault on failure.

    Wrapped in a module function so tests can monkeypatch it (success = returns,
    failure = raises) without a live vCenter.
    """
    from pyVim.task import WaitForTask

    WaitForTask(task)


def _find_vm(si: Any, vm_name: str) -> dict:
    content = get_content(si)
    rows = _retrieve_props(
        content, vim.VirtualMachine, ["name", "config.hardware.device", "runtime.powerState"]
    )
    matches = [r for r in rows if r.get("name") == vm_name]
    if matches:
        # vCenter allows two VMs with one name in different folders. Keep every match's
        # id so the gate can refuse rather than reconfigure whichever came back first.
        return {**matches[0], "_same_name_moids": [r.get("_moid", "") for r in matches]}
    known = sorted(r.get("name", "") for r in rows)[:10]
    hint = f" Known VMs: {', '.join(known)}." if known else ""
    raise GpuNotFoundError(
        f"VM '{vm_name}' not found on this target.{hint} Run list_gpu_consumers to see vGPU VMs."
    )


def _current_profile(devices: list) -> str:
    for device in devices or []:
        profile = _vgpu_backing(device)
        if profile:
            return profile
    return ""


def _build_reconfig_spec(devices: list, profile: str) -> Any:
    """A ConfigSpec that sets the vGPU profile: edit the existing vGPU device, else add one.

    The edit target is selected by ``_vgpu_backing`` (the SAME predicate preview uses),
    NOT by ``isinstance(VirtualPCIPassthrough)`` — a VM's full-GPU DirectPath passthrough
    or an SR-IOV NIC is also a VirtualPCIPassthrough, and editing its backing would silently
    convert it to a vGPU device, outside the stated blast radius (review C1).
    """
    backing = vim.vm.device.VirtualPCIPassthrough.VmiopBackingInfo(vgpu=profile)
    existing = next((d for d in devices or [] if _vgpu_backing(d)), None)
    if existing is not None:
        existing.backing = backing
        change = vim.vm.device.VirtualDeviceSpec(
            operation=vim.vm.device.VirtualDeviceSpec.Operation.edit, device=existing
        )
    else:
        device = vim.vm.device.VirtualPCIPassthrough(backing=backing)
        change = vim.vm.device.VirtualDeviceSpec(
            operation=vim.vm.device.VirtualDeviceSpec.Operation.add, device=device
        )
    # vGPU requires the full memory reservation to be locked.
    return vim.vm.ConfigSpec(deviceChange=[change], memoryReservationLockedToMax=True)


#: The confirm=True refusal hint, and the preview hint. Neither reads as an invitation.
_PREVIEW_HINT = (
    "Nothing was changed. Show blast_radius to the user; re-run with confirm=True only after "
    "they agree to it."
)


class VgpuAssignRefusedError(PrivateAiError):
    """confirm=True refused: a blocker is present or the blast radius is unmeasured."""


def blast_radius(row: dict, vm_name: str, profile: str) -> dict:
    """L1 for assign_vgpu: which VM, how its devices change, and what stands in the way.

    ``devices`` is read as unmeasured when the property is absent, never as "no
    devices": a VM always has devices, and reading a failed read as an empty list
    turns an edit of the existing vGPU into an *add* of a second one.
    """
    devices = row.get("config.hardware.device")
    power_state = row.get("runtime.powerState")
    power = str(power_state) if power_state else None
    same_name = row.get("_same_name_moids") or [row.get("_moid", "")]

    unmeasured = [label for label, value in (("power state", power), ("virtual devices", devices))
                  if value is None]
    blockers = []
    if power == "poweredOn":
        blockers.append(
            f"VM '{vm_name}' is powered on — a vGPU change needs the VM powered off (no hot-add). "
            f"Power it off first (vmware-aiops power_off_vm '{vm_name}'), then preview again."
        )
    elif power == "suspended":
        blockers.append(
            f"VM '{vm_name}' is suspended: its memory holds a paused workload and its devices "
            f"cannot change. Power it off first (vmware-aiops power_off_vm '{vm_name}'), then "
            f"preview again."
        )
    if len(same_name) > 1:
        blockers.append(
            f"{len(same_name)} VMs are named '{vm_name}' ({', '.join(same_name)}), so which one "
            f"would be reconfigured is ambiguous. Rename one of them in vCenter, then preview again."
        )

    current = _current_profile(devices) if devices is not None else None
    return {
        "vm": vm_name,
        "vm_id": row.get("_moid") or None,
        "same_name_vm_ids": same_name,
        "power_state": power,
        "current_profile": _sanitize(current) or None if current is not None else None,
        "target_profile": profile,
        "device_change": None if devices is None else ("edit" if current else "add"),
        "other_passthrough_devices": None if devices is None else sum(
            1 for d in devices
            if isinstance(d, vim.vm.device.VirtualPCIPassthrough) and not _vgpu_backing(d)
        ),
        "memory_reservation_locked_to_max": True,
        "requires_power_off": True,
        "blockers": blockers,
        "unmeasured": unmeasured,
    }


def _refuse(radius: dict, vm_name: str) -> None:
    if radius["blockers"]:
        raise VgpuAssignRefusedError(" ".join(radius["blockers"]) + " Nothing was changed.")
    if radius["unmeasured"]:
        raise VgpuAssignRefusedError(
            f"assign_vgpu refused for '{vm_name}': could not read {', '.join(radius['unmeasured'])}, "
            f"so how the VM would change is unknown. Nothing was changed. Check the VM in vCenter "
            f"(permissions, inaccessible or orphaned state) and preview again."
        )


def assign_vgpu(
    si: Any,
    vm_name: str,
    profile: str,
    *,
    confirm: bool = False,
    audit_logger: Any = None,
    target_name: str = "default",
) -> dict:
    """Set VM ``vm_name``'s vGPU profile to ``profile``.

    confirm=False returns the blast radius (identity, current→target profile, edit or
    add, power state, blockers, unmeasured) without acting. confirm=True applies it —
    but only when nothing blocks it and everything it depends on was read: a running
    or suspended VM, a name shared by several VMs, or an unreadable power state or
    device list is refused with a teaching error.
    """
    if not profile:
        raise PrivateAiError(
            "A target vGPU profile is required (e.g. 'grid_a100-4c'). Run get_gpu_host to see profiles."
        )

    row = _find_vm(si, vm_name)
    radius = blast_radius(row, vm_name, profile)
    devices = row.get("config.hardware.device") or []
    current = _current_profile(devices)

    preview = {
        "vm": vm_name,
        "current_profile": radius["current_profile"],
        "target_profile": profile,
        "power_state": radius["power_state"] or "",
        "requires_power_off": True,
        "applied": False,
        "blast_radius": radius,
    }

    if not confirm:
        return {"action": "preview", **preview, "hint": _PREVIEW_HINT}

    _refuse(radius, vm_name)

    spec = _build_reconfig_spec(devices, profile)
    vm = row.get("_obj")
    # Wait for the reconfigure to actually finish — a fire-and-forget task can fail
    # asynchronously (bad profile, insufficient framebuffer) after we've returned. Report
    # and audit the REAL outcome, never a premature "ok" (review H1).
    try:
        _wait_for_task(vm.ReconfigVM_Task(spec=spec))
    except Exception as exc:  # noqa: BLE001 — surface as a teaching error + audit the failure
        if audit_logger is not None:
            audit_logger.record(
                target=target_name, operation="vgpu_assign", resource=vm_name,
                parameters={"target_profile": profile, "from_profile": current}, result="error",
            )
        raise PrivateAiError(
            f"vGPU assignment failed for '{vm_name}': {type(exc).__name__}. The profile "
            f"'{profile}' may not be offered by the VM's host or the GPU lacks free framebuffer — "
            f"run get_gpu_host on the VM's host to see valid profiles."
        ) from exc
    if audit_logger is not None:
        audit_logger.record(
            target=target_name,
            operation="vgpu_assign",
            resource=vm_name,
            parameters={"target_profile": profile, "from_profile": current},
            result="ok",
        )
    return {"action": "applied", **preview, "applied": True,
            "hint": f"vGPU profile set to {profile}. Power the VM on to use it."}
