"""Regression tests for the vGPU assignment (write) vertical.

Covers the spec-gate (踩坑 #36), the ReconfigVM spec (add vs edit), preview-without-
acting, the powered-on refusal (routing to vmware-aiops), audit on apply, and the
teaching errors for a bad VM / empty profile.
"""

from __future__ import annotations

import importlib.util
import pathlib
from types import SimpleNamespace

import pytest
from pyVmomi import vim

from vmware_privateai.ops import assign
from vmware_privateai.ops._errors import GpuNotFoundError, PrivateAiError


def _load_spec():
    path = pathlib.Path(__file__).parent.parent / "spec" / "privateai_endpoints.py"
    spec = importlib.util.spec_from_file_location("privateai_endpoints", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeVM:
    """A ManagedObject stand-in whose ReconfigVM_Task records the spec it was given."""

    def __init__(self):
        self.reconfigured_with = None

    def ReconfigVM_Task(self, spec):  # noqa: N802 — mirrors the pyVmomi method name
        self.reconfigured_with = spec
        return SimpleNamespace(_moId="task-1")


def _vgpu_dev(profile: str):
    return vim.vm.device.VirtualPCIPassthrough(
        key=0, backing=vim.vm.device.VirtualPCIPassthrough.VmiopBackingInfo(vgpu=profile)
    )


def _patch_vm(monkeypatch, *, power="poweredOff", devices=None, obj=None):
    row = {
        "name": "train-01",
        "config.hardware.device": devices or [],
        "runtime.powerState": power,
        "_obj": obj if obj is not None else _FakeVM(),
    }
    monkeypatch.setattr(assign, "_find_vm", lambda si, name: row if name == "train-01" else _raise_notfound(name))
    return row


def _raise_notfound(name):
    raise GpuNotFoundError(f"VM '{name}' not found on this target. Run gpu_consumer_list to see vGPU VMs.")


# --- spec-gate ------------------------------------------------------------------

def test_assign_declares_only_verified_spec_keys():
    spec = _load_spec()
    assert assign.SPEC_KEYS_USED
    assert assign.SPEC_KEYS_USED <= set(spec.PYVMOMI_OBJECTS)


# --- reconfig spec construction -------------------------------------------------

def test_build_spec_adds_passthrough_when_none_present():
    spec = assign._build_reconfig_spec([], "grid_a100-4c")
    assert spec.memoryReservationLockedToMax is True
    change = spec.deviceChange[0]
    assert change.operation == vim.vm.device.VirtualDeviceSpec.Operation.add
    assert change.device.backing.vgpu == "grid_a100-4c"


def test_build_spec_edits_existing_passthrough():
    spec = assign._build_reconfig_spec([_vgpu_dev("grid_a100-2c")], "grid_a100-4c")
    change = spec.deviceChange[0]
    assert change.operation == vim.vm.device.VirtualDeviceSpec.Operation.edit
    assert change.device.backing.vgpu == "grid_a100-4c"


def test_build_spec_never_edits_a_non_vgpu_passthrough():
    # review C1: a full-GPU DirectPath / SR-IOV device is also a VirtualPCIPassthrough but has no
    # VmiopBackingInfo — it must be left untouched (ADD a vGPU), never edited into a vGPU device.
    fixed = vim.vm.device.VirtualPCIPassthrough(
        key=5, backing=vim.vm.device.VirtualPCIPassthrough.DeviceBackingInfo(id="0000:3b:00.0")
    )
    spec = assign._build_reconfig_spec([fixed], "grid_a100-4c")
    change = spec.deviceChange[0]
    assert change.operation == vim.vm.device.VirtualDeviceSpec.Operation.add  # add, not edit
    assert change.device is not fixed  # the fixed passthrough is NOT clobbered
    assert change.device.backing.vgpu == "grid_a100-4c"


# --- preview vs apply -----------------------------------------------------------

def test_preview_does_not_reconfigure(monkeypatch):
    vm = _FakeVM()
    _patch_vm(monkeypatch, power="poweredOff", devices=[_vgpu_dev("grid_a100-2c")], obj=vm)
    out = assign.assign_vgpu(None, "train-01", "grid_a100-4c", confirm=False)
    assert out["applied"] is False
    assert out["current_profile"] == "grid_a100-2c" and out["target_profile"] == "grid_a100-4c"
    assert out["requires_power_off"] is True
    assert vm.reconfigured_with is None  # nothing applied on a preview


def test_confirm_on_powered_off_applies_and_audits(monkeypatch):
    vm = _FakeVM()
    _patch_vm(monkeypatch, power="poweredOff", obj=vm)
    monkeypatch.setattr(assign, "_wait_for_task", lambda task: None)  # task succeeds
    recorded = {}
    audit = SimpleNamespace(record=lambda **kw: recorded.update(kw))
    out = assign.assign_vgpu(None, "train-01", "grid_a100-4c", confirm=True, audit_logger=audit, target_name="prod")
    assert out["applied"] is True
    assert vm.reconfigured_with is not None  # ReconfigVM_Task was called
    assert recorded["operation"] == "vgpu_assign" and recorded["resource"] == "train-01"
    assert recorded["result"] == "ok"


def test_apply_reports_and_audits_the_real_task_failure(monkeypatch):
    # review H1: a task that fails asynchronously must NOT report success/audit "ok".
    vm = _FakeVM()
    _patch_vm(monkeypatch, power="poweredOff", obj=vm)

    def _boom(task):
        raise RuntimeError("InvalidDeviceSpec: profile not offered by host")

    monkeypatch.setattr(assign, "_wait_for_task", _boom)
    recorded = {}
    audit = SimpleNamespace(record=lambda **kw: recorded.update(kw))
    with pytest.raises(PrivateAiError) as ei:
        assign.assign_vgpu(None, "train-01", "grid_a100-4c", confirm=True, audit_logger=audit, target_name="prod")
    assert "failed" in str(ei.value) and "gpu_host_get" in str(ei.value)
    assert recorded["result"] == "error"  # audited as a failure, not "ok"


def test_confirm_on_running_vm_is_refused(monkeypatch):
    vm = _FakeVM()
    _patch_vm(monkeypatch, power="poweredOn", obj=vm)
    with pytest.raises(PrivateAiError) as ei:
        assign.assign_vgpu(None, "train-01", "grid_a100-4c", confirm=True)
    assert "powered on" in str(ei.value) and "vmware-aiops" in str(ei.value)
    assert vm.reconfigured_with is None  # refused, not applied


# --- teaching errors ------------------------------------------------------------

def test_empty_profile_raises_teaching_error():
    with pytest.raises(PrivateAiError) as ei:
        assign.assign_vgpu(None, "train-01", "", confirm=True)
    assert "profile is required" in str(ei.value)


def test_bad_vm_name_raises_not_found(monkeypatch):
    _patch_vm(monkeypatch, power="poweredOff")
    with pytest.raises(GpuNotFoundError):
        assign.assign_vgpu(None, "does-not-exist", "grid_a100-4c", confirm=False)
