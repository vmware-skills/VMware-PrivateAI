"""vGPU / DirectPath profile catalog (read) — what profiles a host or vCenter offers.

Sources (VERIFIED pyVmomi object paths — see tests/eval/spec/privateai_endpoints.py):
  - ``EnvironmentBrowser.QueryConfigTarget(host)`` -> ConfigTarget.vgpuProfileInfo[]
    (VirtualMachineVgpuProfileInfo: profileName/name/fbSizeInGib/profileClass/profileSharing).
    NOTE: the attribute is ``vgpuProfileInfo`` on pyVmomi 9.1 — the spec doc's ``vgpu[]`` does
    not exist on vim.vm.ConfigTarget (a phantom, 踩坑 #36); code reads ``vgpuProfileInfo``
    with a ``vgpu`` fallback and treats an absent attribute as "no profiles" (踩坑 形态 #1).
  - ``content.directPathProfileManager.ListDirectPathProfiles`` -> DirectPathProfileInfo[]
    (vCenter-level MO, NEW in vSphere 9.0; absent on 8.x — that gap is taught, not crashed).

Every field is read defensively (getattr) — this is a beta skill whose response shapes are
not yet confirmed on live 9.x hardware. QueryConfigTarget is a per-host call, so a `host`
filter scopes the catalog to one host instead of polling the whole estate.
"""

from __future__ import annotations

from typing import Any

from pyVmomi import vim

from vmware_privateai.connection import get_content
from vmware_privateai.ops._errors import GpuNotFoundError
from vmware_privateai.ops._paging import envelope
from vmware_privateai.ops._sanitize import _sanitize
from vmware_privateai.ops.gpu import _matches, _retrieve_props

# Spec keys this module is allowed to touch — asserted ⊆ PYVMOMI_OBJECTS by the gate (踩坑 #36).
SPEC_KEYS_USED: frozenset[str] = frozenset({"vgpu_profile_catalog", "directpath_profiles"})


def _project_profile(vp: Any) -> dict:
    """Project one ``VirtualMachineVgpuProfileInfo`` into an agent-facing profile summary.

    ``profileName`` is the canonical id (e.g. "nvidia_a100-4c"); ``fbSizeInGib`` is the
    framebuffer size in GiB. Every field degrades to a neutral value if absent.
    """
    fb = getattr(vp, "fbSizeInGib", None)
    # Every string here is host-authored profile text reaching the agent — sanitize (踩坑 注入防护).
    return {
        "profile": _sanitize(getattr(vp, "profileName", "") or getattr(vp, "name", "") or ""),
        "name": _sanitize(getattr(vp, "name", "") or ""),
        "framebuffer_gib": fb if isinstance(fb, (int, float)) else None,
        "profile_class": _sanitize(getattr(vp, "profileClass", "") or ""),
        "sharing": _sanitize(getattr(vp, "profileSharing", "") or ""),
        "vendor_id": _sanitize(getattr(vp, "deviceVendorId", "") or ""),
    }


def _vgpu_infos(config_target: Any) -> list:
    """The VgpuProfileInfo list on a ConfigTarget — ``vgpuProfileInfo``, else ``vgpu``, else []."""
    return list(getattr(config_target, "vgpuProfileInfo", None) or getattr(config_target, "vgpu", None) or [])


def _query_config_target(host_mo: Any) -> Any:
    """QueryConfigTarget for one host via its parent ComputeResource's EnvironmentBrowser.

    HostSystem has no ``environmentBrowser`` of its own — it lives on the parent
    ComputeResource. Returns the ConfigTarget, or None if no browser is reachable.
    """
    parent = getattr(host_mo, "parent", None)
    browser = getattr(parent, "environmentBrowser", None)
    if browser is None:
        return None
    return browser.QueryConfigTarget(host=host_mo)


def list_vgpu_profiles(
    si: Any,
    *,
    host: str | None = None,
    model: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """List the available vGPU profile catalog, aggregated across hosts. Paginated.

    Each item is a distinct profile with the hosts that offer it (``hosts`` / ``host_count``),
    filtered by host name and by profile/model substring. Scope to one host with ``host`` to
    avoid a per-host QueryConfigTarget across the whole estate. Hosts whose per-host query
    fails (disconnected / not responding) are listed in ``unreachable_hosts`` rather than
    failing the whole call — an empty catalog with a populated ``unreachable_hosts`` is a
    reachability problem, not "no profiles" (踩坑 形态 #1).
    """
    content = get_content(si)
    rows = _retrieve_props(content, vim.HostSystem, ["name"])
    catalog: dict[str, dict] = {}
    unreachable: list[str] = []
    for r in rows:
        host_name = _sanitize(r.get("name", "") or "")
        if not _matches(host_name, host):
            continue
        # QueryConfigTarget (and host.parent.environmentBrowser) is a per-host RPC: a single
        # disconnected / not-responding host must NOT sink the whole catalog. Collect it into
        # unreachable_hosts and keep aggregating the rest (degrade gracefully per-host, review M2).
        try:
            infos = _vgpu_infos(_query_config_target(r.get("_obj")))
        except Exception:  # noqa: BLE001 — any per-host RPC fault degrades to "unreachable", not a crash
            unreachable.append(host_name)
            continue
        for vp in infos:
            proj = _project_profile(vp)
            if model and not _matches(f"{proj['profile']} {proj['name']}", model):
                continue
            entry = catalog.setdefault(proj["profile"], {**proj, "_hosts": set()})
            entry["_hosts"].add(host_name)
    items: list[dict] = []
    for entry in catalog.values():
        hosts = sorted(h for h in entry.pop("_hosts") if h)
        items.append({**entry, "host_count": len(hosts), "hosts": hosts})
    items.sort(key=lambda p: p["profile"])
    result = envelope(items, limit=limit, offset=offset)
    result["unreachable_hosts"] = sorted(set(unreachable))
    return result


def _project_directpath(info: Any) -> dict:
    return {
        "id": _sanitize(getattr(info, "id", "") or ""),
        "name": _sanitize(getattr(info, "name", "") or ""),
        "vendor": _sanitize(getattr(info, "vendorName", "") or ""),
        "description": _sanitize(getattr(info, "description", "") or ""),
    }


def list_directpath_profiles(
    si: Any,
    *,
    name: str | None = None,
    vendor: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """List vCenter-level DirectPath (dynamic passthrough) profiles. vSphere 9.0+. Paginated.

    An empty envelope means the vCenter has zero DirectPath profiles (checked-and-none). If the
    DirectPathProfileManager is absent, this vCenter predates 9.0 — a teaching error routes the
    operator to ``vgpu_profile_list`` rather than reading absence as "no profiles" (踩坑 形态 #1).
    """
    content = get_content(si)
    dppm = getattr(content, "directPathProfileManager", None)
    if dppm is None:
        raise GpuNotFoundError(
            "DirectPath profiles need vCenter 9.0+ (DirectPathProfileManager is not present on "
            "this vCenter). Use vgpu_profile_list for the vGPU profile catalog, which works on 8.x."
        )
    infos = dppm.ListDirectPathProfiles(filterSpec=None) or []
    profiles = [_project_directpath(i) for i in infos]
    profiles = [
        p for p in profiles if _matches(p["name"], name) and _matches(p["vendor"], vendor)
    ]
    profiles.sort(key=lambda p: (p["name"], p["id"]))
    return envelope(profiles, limit=limit, offset=offset)
