"""Regression tests for gpu_host_readiness (verified pyVmomi paths, no live vCenter).

Covers the vgpu_ready verdict logic (GPU present + sharedDirect mode + profiles offered), the
blocking_reasons for each failure mode, the honest driver_note (spec NO_API), only-GPU-hosts
scoping, per-host unreachable degradation (踩坑 形态 #1), and the spec-key gate.
"""

from __future__ import annotations

import importlib.util
import pathlib
from types import SimpleNamespace

from vmware_privateai.ops import readiness


def _load_spec():
    path = pathlib.Path(__file__).parent.parent / "spec" / "privateai_endpoints.py"
    spec = importlib.util.spec_from_file_location("privateai_endpoints", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _gi(gtype: str, mem_mb: int = 16384, vms: int = 0):
    return SimpleNamespace(
        deviceName="NVIDIA A100",
        graphicsType=gtype,
        vendorName="NVIDIA",
        memorySizeInKB=mem_mb * 1024,
        pciId="0000:3b:00.0",
        vm=[object()] * vms,
    )


def _gconfig(default_type: str):
    return SimpleNamespace(hostDefaultGraphicsType=default_type, sharedPassthruAssignmentPolicy="performance")


def _host(name, gtype="sharedDirect", default_type="sharedDirect", vms=0):
    return {
        "name": name,
        "config.graphicsInfo": [_gi(gtype, vms=vms)],
        "config.graphicsConfig": _gconfig(default_type),
        "_obj": SimpleNamespace(),
    }


def _fake(monkeypatch, rows, profiles_by_host=None):
    monkeypatch.setattr(readiness, "get_content", lambda si: object())
    monkeypatch.setattr(readiness, "_retrieve_props", lambda content, obj_type, paths: rows)
    profiles_by_host = profiles_by_host or {}

    def _qct(host_mo):
        n = profiles_by_host.get(id(host_mo), 1)
        return SimpleNamespace(vgpuProfileInfo=[SimpleNamespace()] * n)

    monkeypatch.setattr(readiness, "_query_config_target", _qct)


def test_spec_keys_are_verified():
    spec = _load_spec()
    assert readiness.SPEC_KEYS_USED - set(spec.PYVMOMI_OBJECTS) == set()


def test_ready_host_when_gpu_shareddirect_and_profiles_offered(monkeypatch):
    _fake(monkeypatch, [_host("esx-ok", vms=2)])
    out = readiness.gpu_host_readiness(None)
    item = out["items"][0]
    assert item["vgpu_ready"] is True
    assert item["blocking_reasons"] == []
    assert item["default_graphics_type"] == "sharedDirect"
    assert item["active_vgpu_vms"] == 2
    assert "nvidia-smi" in item["driver_note"]  # honest NO_API note is always present


def test_not_ready_when_default_type_is_vsga(monkeypatch):
    _fake(monkeypatch, [_host("esx-vsga", default_type="shared")])
    item = readiness.gpu_host_readiness(None)["items"][0]
    assert item["vgpu_ready"] is False
    assert any("sharedDirect" in r for r in item["blocking_reasons"])


def test_not_ready_when_no_profiles_offered(monkeypatch):
    row = _host("esx-nodriver")
    _fake(monkeypatch, [row], profiles_by_host={id(row["_obj"]): 0})
    item = readiness.gpu_host_readiness(None)["items"][0]
    assert item["vgpu_ready"] is False
    assert any("no vGPU profiles" in r for r in item["blocking_reasons"])


def test_non_gpu_hosts_are_excluded(monkeypatch):
    plain = {
        "name": "esx-plain",
        "config.graphicsInfo": [_gi("basic")],
        "config.graphicsConfig": _gconfig("shared"),
        "_obj": SimpleNamespace(),
    }
    _fake(monkeypatch, [plain, _host("esx-gpu")])
    out = readiness.gpu_host_readiness(None)
    assert out["total"] == 1 and out["items"][0]["host"] == "esx-gpu"


def test_unreachable_host_is_degraded_not_crashed(monkeypatch):
    row = _host("esx-flaky")
    monkeypatch.setattr(readiness, "get_content", lambda si: object())
    monkeypatch.setattr(readiness, "_retrieve_props", lambda content, obj_type, paths: [row])

    def _boom(host_mo):
        raise RuntimeError("host not responding")

    monkeypatch.setattr(readiness, "_query_config_target", _boom)
    out = readiness.gpu_host_readiness(None)
    assert out["unreachable_hosts"] == ["esx-flaky"]
    assert out["items"][0]["profile_query_failed"] is True
    assert out["items"][0]["vgpu_ready"] is False
    # review L1: an unreachable host must NOT be reported as "no vGPU profiles / driver missing".
    reasons = out["items"][0]["blocking_reasons"]
    assert any("unreachable" in r for r in reasons)
    assert not any("driver may be missing" in r for r in reasons)
