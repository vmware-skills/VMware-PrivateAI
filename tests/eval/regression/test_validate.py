"""Regression tests for vgpu_profile_validate (read-only vGPU-change pre-flight).

Covers the two ReconfigVM failure modes it exists to catch up front — VM powered on, and the
host not offering the target profile — plus the happy path, the bad-VM teaching error, and the
spec-key gate. No live vCenter (mocked pyVmomi).
"""

from __future__ import annotations

import importlib.util
import pathlib
from types import SimpleNamespace

import pytest
from pyVmomi import vim

from vmware_privateai.ops import validate
from vmware_privateai.ops._errors import GpuNotFoundError


def _load_spec():
    path = pathlib.Path(__file__).parent.parent / "spec" / "privateai_endpoints.py"
    spec = importlib.util.spec_from_file_location("privateai_endpoints", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _vgpu_device(profile: str):
    return vim.vm.device.VirtualPCIPassthrough(
        key=0, backing=vim.vm.device.VirtualPCIPassthrough.VmiopBackingInfo(vgpu=profile)
    )


def _profile(name: str, fb: int = 4):
    return SimpleNamespace(
        profileName=name,
        name=name,
        fbSizeInGib=fb,
        profileClass="Compute",
        profileSharing="timeSliced",
        deviceVendorId="0x10de",
    )


def _vm_row(power="poweredOff", current="nvidia_a100-4c"):
    return {
        "name": "train-01",
        "config.hardware.device": [_vgpu_device(current)] if current else [],
        "runtime.powerState": power,
        "runtime.host": SimpleNamespace(name="esx-01"),
        "_obj": SimpleNamespace(),
    }


def _fake(monkeypatch, row, offered):
    monkeypatch.setattr(validate, "get_content", lambda si: object())
    monkeypatch.setattr(validate, "_retrieve_props", lambda content, obj_type, paths: [row])
    monkeypatch.setattr(validate, "_query_config_target", lambda host_mo: SimpleNamespace(vgpuProfileInfo=offered))


def test_spec_keys_are_verified():
    spec = _load_spec()
    assert validate.SPEC_KEYS_USED - set(spec.PYVMOMI_OBJECTS) == set()


def test_can_apply_when_off_and_host_offers_profile(monkeypatch):
    _fake(monkeypatch, _vm_row(power="poweredOff"), [_profile("nvidia_a100-8c", fb=8)])
    out = validate.validate_vgpu_change(None, "train-01", "nvidia_a100-8c")
    assert out["can_apply"] is True
    assert out["blocking_reasons"] == []
    assert out["host_offers_target"] is True
    assert out["target_framebuffer_gib"] == 8
    assert out["current_profile"] == "nvidia_a100-4c"


def test_blocks_when_vm_powered_on(monkeypatch):
    _fake(monkeypatch, _vm_row(power="poweredOn"), [_profile("nvidia_a100-8c")])
    out = validate.validate_vgpu_change(None, "train-01", "nvidia_a100-8c")
    assert out["can_apply"] is False
    assert any("powered on" in r for r in out["blocking_reasons"])


def test_blocks_when_host_does_not_offer_profile(monkeypatch):
    _fake(monkeypatch, _vm_row(power="poweredOff"), [_profile("nvidia_a100-4c")])
    out = validate.validate_vgpu_change(None, "train-01", "nvidia_h100-16c")
    assert out["can_apply"] is False
    assert out["host_offers_target"] is False
    assert any("does not offer" in r and "nvidia_a100-4c" in r for r in out["blocking_reasons"])


def test_bad_vm_name_raises_teaching_error(monkeypatch):
    monkeypatch.setattr(validate, "get_content", lambda si: object())
    monkeypatch.setattr(validate, "_retrieve_props", lambda content, obj_type, paths: [_vm_row()])
    with pytest.raises(GpuNotFoundError) as ei:
        validate.validate_vgpu_change(None, "typo-vm", "nvidia_a100-4c")
    assert "typo-vm" in str(ei.value) and "gpu_consumer_list" in str(ei.value)


def test_empty_target_profile_raises_teaching_error(monkeypatch):
    with pytest.raises(GpuNotFoundError):
        validate.validate_vgpu_change(None, "train-01", "")


def test_null_runtime_host_is_unknown_not_a_false_negative(monkeypatch):
    # review M1 / 踩坑 形态 #1: a null runtime.host must not be asserted as "host lacks the profile".
    row = _vm_row(power="poweredOff")
    row["runtime.host"] = None
    monkeypatch.setattr(validate, "get_content", lambda si: object())
    monkeypatch.setattr(validate, "_retrieve_props", lambda content, obj_type, paths: [row])
    out = validate.validate_vgpu_change(None, "train-01", "nvidia_a100-8c")
    assert out["host_offers_target"] is None  # unknown, not False
    assert out["can_apply"] is False
    assert any("could not determine the VM's host" in r for r in out["blocking_reasons"])
