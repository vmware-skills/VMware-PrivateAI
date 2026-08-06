"""Regression tests for the GPU inventory vertical.

Covers the anti-phantom-endpoint gate (踩坑 #36), field projection, filtering,
pagination, the teaching error on a bad host id, vGPU-backing extraction, and the
empty-result envelope (踩坑 形态 #1: empty must read as checked-and-none).
"""

from __future__ import annotations

import importlib.util
import pathlib
from types import SimpleNamespace

import pytest
from pyVmomi import vim

from vmware_privateai.ops import gpu
from vmware_privateai.ops._errors import GpuNotFoundError


def _load_spec():
    path = pathlib.Path(__file__).parent.parent / "spec" / "privateai_endpoints.py"
    spec = importlib.util.spec_from_file_location("privateai_endpoints", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _gi(device: str, gtype: str, vendor: str = "NVIDIA", mem_mb: int = 16384, pci: str = "0000:3b:00.0", vms: int = 0):
    """A fake vim.host.GraphicsInfo — projection reads it via getattr, so a namespace suffices."""
    return SimpleNamespace(
        deviceName=device,
        graphicsType=gtype,
        vendorName=vendor,
        memorySizeInKB=mem_mb * 1024,
        pciId=pci,
        vm=[object()] * vms,
    )


# --- anti-phantom-endpoint gate -------------------------------------------------

def test_ops_gpu_declares_only_verified_spec_keys():
    spec = _load_spec()
    assert gpu.SPEC_KEYS_USED, "SPEC_KEYS_USED must not be empty — a gate that checks nothing is worse than none"
    missing = gpu.SPEC_KEYS_USED - set(spec.PYVMOMI_OBJECTS)
    assert not missing, f"ops/gpu references spec keys absent from the verified index (踩坑 #36): {missing}"


# --- projection & GPU filtering -------------------------------------------------

def test_project_gpu_maps_fields_and_converts_memory():
    out = gpu._project_gpu(_gi("NVIDIA A100", "sharedDirect", mem_mb=40960, vms=3))
    assert out == {
        "device": "NVIDIA A100",
        "type": "sharedDirect",
        "vendor": "NVIDIA",
        "pci_id": "0000:3b:00.0",
        "memory_mb": 40960,
        "vm_count": 3,
    }


def test_basic_framebuffer_is_not_counted_as_a_gpu():
    props = {"name": "esx-01", "config.graphicsInfo": [_gi("Onboard VGA", "basic", vendor="Matrox")]}
    assert gpu._gpus_of(props) == []


def test_missing_graphicsinfo_degrades_to_empty_not_crash():
    # 踩坑 形态 #1: an absent field must degrade to empty, never raise.
    assert gpu._gpus_of({"name": "esx-01"}) == []


# --- list_gpu_hosts -------------------------------------------------------------

def _fake_hosts(monkeypatch, rows):
    monkeypatch.setattr(gpu, "get_content", lambda si: object())
    monkeypatch.setattr(gpu, "_retrieve_props", lambda content, obj_type, paths: rows)


def test_list_gpu_hosts_only_returns_hosts_with_a_gpu(monkeypatch):
    _fake_hosts(
        monkeypatch,
        [
            {"name": "esx-gpu", "config.graphicsInfo": [_gi("A100", "sharedDirect", vms=2)]},
            {"name": "esx-plain", "config.graphicsInfo": [_gi("VGA", "basic")]},
            {"name": "esx-empty", "config.graphicsInfo": []},
        ],
    )
    out = gpu.list_gpu_hosts(None)
    assert out["total"] == 1
    assert out["items"][0]["host"] == "esx-gpu"
    assert out["items"][0]["vendors"] == ["NVIDIA"]
    assert out["items"][0]["vgpu_vms"] == 2


def test_list_gpu_hosts_filters_by_name_and_vendor(monkeypatch):
    _fake_hosts(
        monkeypatch,
        [
            {"name": "esx-a", "config.graphicsInfo": [_gi("A100", "direct", vendor="NVIDIA")]},
            {"name": "esx-b", "config.graphicsInfo": [_gi("MI300", "direct", vendor="AMD")]},
        ],
    )
    assert gpu.list_gpu_hosts(None, vendor="nvidia")["total"] == 1
    assert gpu.list_gpu_hosts(None, name="esx-b")["items"][0]["host"] == "esx-b"


def test_list_gpu_hosts_paginates(monkeypatch):
    rows = [{"name": f"esx-{i:02d}", "config.graphicsInfo": [_gi("A100", "direct")]} for i in range(5)]
    _fake_hosts(monkeypatch, rows)
    out = gpu.list_gpu_hosts(None, limit=2, offset=0)
    assert out["returned"] == 2 and out["total"] == 5 and out["truncated"] is True and out["hint"]


def test_empty_result_is_not_truncated(monkeypatch):
    # 踩坑 形态 #1: empty items with truncated=False = checked-and-none, not a failure.
    _fake_hosts(monkeypatch, [])
    out = gpu.list_gpu_hosts(None)
    assert out["items"] == [] and out["total"] == 0 and out["truncated"] is False


# --- get_gpu_host ---------------------------------------------------------------

def test_get_gpu_host_bad_name_raises_teaching_error(monkeypatch):
    _fake_hosts(monkeypatch, [{"name": "esx-gpu", "config.graphicsInfo": [_gi("A100", "direct")]}])
    with pytest.raises(GpuNotFoundError) as ei:
        gpu.get_gpu_host(None, "esx-typo")
    msg = str(ei.value)
    assert "esx-typo" in msg and "gpu_host_list" in msg and "esx-gpu" in msg  # names the fix + lists real hosts


# --- gpu consumers (vGPU backing) ----------------------------------------------

def _vgpu_device(profile: str):
    return vim.vm.device.VirtualPCIPassthrough(
        key=0, backing=vim.vm.device.VirtualPCIPassthrough.VmiopBackingInfo(vgpu=profile)
    )


def test_vgpu_backing_extracts_profile_and_ignores_non_vgpu():
    assert gpu._vgpu_backing(_vgpu_device("grid_a100-4c")) == "grid_a100-4c"
    assert gpu._vgpu_backing(vim.vm.device.VirtualDisk(key=1)) == ""


def test_list_gpu_consumers_finds_vgpu_vms(monkeypatch):
    monkeypatch.setattr(gpu, "get_content", lambda si: object())
    monkeypatch.setattr(
        gpu,
        "_retrieve_props",
        lambda content, obj_type, paths: [
            {"name": "train-01", "config.hardware.device": [_vgpu_device("grid_a100-4c")]},
            {"name": "web-01", "config.hardware.device": [vim.vm.device.VirtualDisk(key=1)]},
        ],
    )
    out = gpu.list_gpu_consumers(None)
    assert out["total"] == 1
    assert out["items"][0] == {"vm": "train-01", "profile": "grid_a100-4c"}
