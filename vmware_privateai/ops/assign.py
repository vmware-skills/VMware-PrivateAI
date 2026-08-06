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
    for r in rows:
        if r.get("name") == vm_name:
            return r
    known = sorted(r.get("name", "") for r in rows)[:10]
    hint = f" Known VMs: {', '.join(known)}." if known else ""
    raise GpuNotFoundError(
        f"VM '{vm_name}' not found on this target.{hint} Run gpu_consumer_list to see vGPU VMs."
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

    confirm=False previews the blast radius (current→target profile, power state,
    that a power-off is required) without acting. confirm=True applies it — but only
    if the VM is powered off; a running VM is refused with a teaching error.
    """
    if not profile:
        raise PrivateAiError(
            "A target vGPU profile is required (e.g. 'grid_a100-4c'). Run gpu_host_get to see profiles."
        )

    row = _find_vm(si, vm_name)
    devices = row.get("config.hardware.device") or []
    current = _current_profile(devices)
    power_state = str(row.get("runtime.powerState") or "")
    running = power_state == "poweredOn"

    preview = {
        "vm": vm_name,
        "current_profile": current or None,
        "target_profile": profile,
        "power_state": power_state,
        "requires_power_off": True,
        "applied": False,
    }

    if not confirm:
        preview["hint"] = "Preview only. Power the VM off, then re-run with confirm=true to apply."
        return preview

    if running:
        raise PrivateAiError(
            f"VM '{vm_name}' is powered on — a vGPU change needs the VM powered off (no hot-add). "
            f"Power it off first (vmware-aiops vm_power_off '{vm_name}'), then re-run with confirm=true."
        )

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
            f"run gpu_host_get on the VM's host to see valid profiles."
        ) from exc
    if audit_logger is not None:
        audit_logger.record(
            target=target_name,
            operation="vgpu_assign",
            resource=vm_name,
            parameters={"target_profile": profile, "from_profile": current},
            result="ok",
        )
    return {**preview, "applied": True, "hint": f"vGPU profile set to {profile}. Power the VM on to use it."}
