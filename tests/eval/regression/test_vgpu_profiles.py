"""Regression tests for the vGPU / DirectPath profile-catalog vertical.

Covers the anti-phantom-endpoint gate (踩坑 #36), the ``vgpuProfileInfo`` attribute + its
``vgpu`` fallback, defensive projection, cross-host aggregation, the version-gap teaching
error on DirectPath, and the empty-result envelope (踩坑 形态 #1).
"""

from __future__ import annotations

import importlib.util
import pathlib
from types import SimpleNamespace

import pytest

from vmware_privateai.ops import vgpu_profiles as vp
from vmware_privateai.ops._errors import GpuNotFoundError


def _load_spec():
    path = pathlib.Path(__file__).parent.parent / "spec" / "privateai_endpoints.py"
    spec = importlib.util.spec_from_file_location("privateai_endpoints", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _profile(profile_name: str, fb: int = 4, cls: str = "Compute", vendor: str = "0x10de"):
    return SimpleNamespace(
        profileName=profile_name,
        name=profile_name.replace("_", " "),
        fbSizeInGib=fb,
        profileClass=cls,
        profileSharing="timeSliced",
        deviceVendorId=vendor,
    )


# --- anti-phantom-endpoint gate -------------------------------------------------

def test_ops_declares_only_verified_spec_keys():
    spec = _load_spec()
    assert vp.SPEC_KEYS_USED, "a gate that checks nothing is worse than none"
    missing = vp.SPEC_KEYS_USED - set(spec.PYVMOMI_OBJECTS)
    assert not missing, f"vgpu_profiles references spec keys absent from the verified index: {missing}"


# --- projection & the vgpuProfileInfo attribute --------------------------------

def test_project_profile_maps_fields():
    out = vp._project_profile(_profile("nvidia_a100-4c", fb=4, cls="Compute"))
    assert out["profile"] == "nvidia_a100-4c"
    assert out["framebuffer_gib"] == 4
    assert out["profile_class"] == "Compute"
    assert out["sharing"] == "timeSliced"


def test_vgpu_infos_reads_vgpuprofileinfo_then_falls_back_to_vgpu():
    # Real 9.1 attribute:
    ct = SimpleNamespace(vgpuProfileInfo=[_profile("a"), _profile("b")])
    assert len(vp._vgpu_infos(ct)) == 2
    # Fallback (spec doc's `vgpu` name), only used when vgpuProfileInfo is absent:
    ct2 = SimpleNamespace(vgpu=[_profile("c")])
    assert len(vp._vgpu_infos(ct2)) == 1


def test_vgpu_infos_absent_or_none_degrades_to_empty():
    # 踩坑 形态 #1: an absent attribute / None ConfigTarget must degrade to [], never crash.
    assert vp._vgpu_infos(None) == []
    assert vp._vgpu_infos(SimpleNamespace()) == []


# --- aggregation across hosts ---------------------------------------------------

def _fake_hosts(monkeypatch, host_to_profiles):
    """host_to_profiles: {host_name: [profile, ...]}. Wires get_content/_retrieve_props/QCT."""
    rows = [{"name": h, "_obj": SimpleNamespace(_name=h)} for h in host_to_profiles]
    monkeypatch.setattr(vp, "get_content", lambda si: object())
    monkeypatch.setattr(vp, "_retrieve_props", lambda content, obj_type, paths: rows)
    monkeypatch.setattr(
        vp,
        "_query_config_target",
        lambda host_mo: SimpleNamespace(vgpuProfileInfo=host_to_profiles[host_mo._name]),
    )


def test_list_vgpu_profiles_aggregates_hosts_per_profile(monkeypatch):
    _fake_hosts(
        monkeypatch,
        {
            "esx-01": [_profile("nvidia_a100-4c"), _profile("nvidia_a100-8c")],
            "esx-02": [_profile("nvidia_a100-4c")],
        },
    )
    out = vp.list_vgpu_profiles(None)
    assert out["total"] == 2
    a100_4c = next(i for i in out["items"] if i["profile"] == "nvidia_a100-4c")
    assert a100_4c["host_count"] == 2 and a100_4c["hosts"] == ["esx-01", "esx-02"]
    a100_8c = next(i for i in out["items"] if i["profile"] == "nvidia_a100-8c")
    assert a100_8c["host_count"] == 1


def test_list_vgpu_profiles_filters_by_model_and_host(monkeypatch):
    _fake_hosts(
        monkeypatch,
        {"esx-01": [_profile("nvidia_a100-4c"), _profile("nvidia_l40-8c")]},
    )
    assert vp.list_vgpu_profiles(None, model="l40")["total"] == 1
    assert vp.list_vgpu_profiles(None, host="nope")["total"] == 0


def test_list_vgpu_profiles_empty_is_not_truncated(monkeypatch):
    # 踩坑 形态 #1: empty items + truncated=False = checked-and-none.
    _fake_hosts(monkeypatch, {})
    out = vp.list_vgpu_profiles(None)
    assert out["items"] == [] and out["total"] == 0 and out["truncated"] is False
    assert out["unreachable_hosts"] == []


def test_list_vgpu_profiles_degrades_on_one_disconnected_host(monkeypatch):
    # review M2: one host whose per-host QueryConfigTarget raises must NOT sink the whole call —
    # it lands in unreachable_hosts while the reachable hosts still aggregate.
    rows = [{"name": h, "_obj": SimpleNamespace(_name=h)} for h in ("esx-ok", "esx-down")]
    monkeypatch.setattr(vp, "get_content", lambda si: object())
    monkeypatch.setattr(vp, "_retrieve_props", lambda content, obj_type, paths: rows)

    def _qct(host_mo):
        if host_mo._name == "esx-down":
            raise RuntimeError("host not responding")
        return SimpleNamespace(vgpuProfileInfo=[_profile("nvidia_a100-4c")])

    monkeypatch.setattr(vp, "_query_config_target", _qct)
    out = vp.list_vgpu_profiles(None)
    assert out["total"] == 1 and out["items"][0]["profile"] == "nvidia_a100-4c"
    assert out["items"][0]["hosts"] == ["esx-ok"]
    assert out["unreachable_hosts"] == ["esx-down"]


# --- DirectPath (vCenter-level, 9.0+) ------------------------------------------

def _dpp(id_: str, name: str, vendor: str = "NVIDIA"):
    return SimpleNamespace(id=id_, name=name, vendorName=vendor, description=f"{name} desc")


def test_list_directpath_profiles_projects_and_filters(monkeypatch):
    dppm = SimpleNamespace(
        ListDirectPathProfiles=lambda filterSpec: [_dpp("1", "A100-DP"), _dpp("2", "L40-DP", vendor="NVIDIA")]
    )
    monkeypatch.setattr(vp, "get_content", lambda si: SimpleNamespace(directPathProfileManager=dppm))
    out = vp.list_directpath_profiles(None)
    assert out["total"] == 2 and out["items"][0]["name"] == "A100-DP"
    assert vp.list_directpath_profiles(None, name="l40")["total"] == 1


def test_list_directpath_profiles_missing_manager_raises_teaching_error(monkeypatch):
    # vSphere 8.x — no DirectPathProfileManager. Must teach + route, not read as "no profiles".
    monkeypatch.setattr(vp, "get_content", lambda si: SimpleNamespace())
    with pytest.raises(GpuNotFoundError) as ei:
        vp.list_directpath_profiles(None)
    msg = str(ei.value)
    assert "9.0" in msg and "vgpu_profile_list" in msg


def test_list_directpath_profiles_empty_is_checked_and_none(monkeypatch):
    dppm = SimpleNamespace(ListDirectPathProfiles=lambda filterSpec: [])
    monkeypatch.setattr(vp, "get_content", lambda si: SimpleNamespace(directPathProfileManager=dppm))
    out = vp.list_directpath_profiles(None)
    assert out["items"] == [] and out["total"] == 0 and out["truncated"] is False
