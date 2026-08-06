"""GPU inventory (read) — hosts, physical GPU devices, and vGPU consumers.

Sources (all VERIFIED pyVmomi object paths — see tests/eval/spec/privateai_endpoints.py):
  - HostSystem ``config.graphicsInfo``  → per-GPU device / type / memory / consumers
  - VirtualMachine ``config.hardware.device`` → VirtualPCIPassthrough vGPU backing

Properties are pulled with a single PropertyCollector batch per object type, not a
per-object lazy fetch (踩坑 #31: N+1 lazy attribute access times out on large estates).
Every field is read with defensive ``getattr`` — this is a beta skill whose response
shapes are not yet confirmed on live 9.x hardware (踩坑 形态 #1: an absent field must
degrade to empty, never crash).
"""

from __future__ import annotations

from typing import Any

from pyVmomi import vim, vmodl

from vmware_privateai.connection import get_content
from vmware_privateai.ops._errors import GpuNotFoundError
from vmware_privateai.ops._paging import envelope
from vmware_privateai.ops._sanitize import _sanitize

# Spec keys this module is allowed to touch — asserted ⊆ PYVMOMI_OBJECTS by the
# regression gate (踩坑 #36: code may only reach verified object paths).
SPEC_KEYS_USED: frozenset[str] = frozenset({"gpu_host_graphics_info", "vm_vgpu_consumer"})

# vim.host.GraphicsInfo.graphicsType values that mean an accelerated / shareable GPU
# (as opposed to the basic framebuffer every host reports).
_GPU_GRAPHICS_TYPES = frozenset({"shared", "direct", "sharedDirect"})


def _retrieve_props(content: Any, obj_type: Any, paths: list[str]) -> list[dict]:
    """Batch-retrieve ``paths`` for every object of ``obj_type`` in one round-trip.

    Returns a list of ``{path: value, "_name": ..., "_moid": ...}`` dicts. One
    PropertyCollector call, not N per-object fetches (踩坑 #31).
    """
    view = content.viewManager.CreateContainerView(content.rootFolder, [obj_type], True)
    try:
        traversal = vmodl.query.PropertyCollector.TraversalSpec(
            name="toView", type=vim.view.ContainerView, path="view", skip=False
        )
        obj_spec = vmodl.query.PropertyCollector.ObjectSpec(obj=view, skip=True, selectSet=[traversal])
        prop_spec = vmodl.query.PropertyCollector.PropertySpec(type=obj_type, pathSet=paths, all=False)
        filter_spec = vmodl.query.PropertyCollector.FilterSpec(objectSet=[obj_spec], propSet=[prop_spec])
        rows: list[dict] = []
        for obj in content.propertyCollector.RetrieveContents([filter_spec]) or []:
            props = {p.name: p.val for p in (getattr(obj, "propSet", None) or [])}
            mo = getattr(obj, "obj", None)
            props["_moid"] = getattr(mo, "_moId", "")
            props["_obj"] = mo  # the managed object itself — needed as a QueryPerf entity
            rows.append(props)
        return rows
    finally:
        view.Destroy()


def _project_gpu(gi: Any) -> dict:
    """Project one ``vim.host.GraphicsInfo`` into an agent-facing GPU summary."""
    mem_kb = getattr(gi, "memorySizeInKB", 0) or 0
    return {
        # device/vendor are attacker-influenceable strings from the host — sanitize (踩坑 注入防护).
        "device": _sanitize(getattr(gi, "deviceName", "") or ""),
        "type": getattr(gi, "graphicsType", "") or "",
        "vendor": _sanitize(getattr(gi, "vendorName", "") or ""),
        "pci_id": getattr(gi, "pciId", "") or "",
        "memory_mb": int(mem_kb) // 1024,
        "vm_count": len(getattr(gi, "vm", None) or []),
    }


def _gpus_of(host_props: dict) -> list[dict]:
    """The accelerated GPUs on one host — only shared/direct/sharedDirect graphicsType.

    The plain "basic" framebuffer every host reports is NOT a compute GPU and is
    excluded. (Beta: if real 9.x hardware surfaces a GPU under an unexpected type,
    widen ``_GPU_GRAPHICS_TYPES`` after first-run verification — 踩坑 #36.)
    """
    infos = host_props.get("config.graphicsInfo") or []
    gpus = [_project_gpu(gi) for gi in infos]
    return [g for g in gpus if g["type"] in _GPU_GRAPHICS_TYPES]


def _host_summary(host_props: dict) -> dict:
    gpus = _gpus_of(host_props)
    return {
        "host": _sanitize(host_props.get("name", "") or ""),
        "gpu_count": len(gpus),
        "vendors": sorted({g["vendor"] for g in gpus if g["vendor"]}),
        "vgpu_vms": sum(g["vm_count"] for g in gpus),
    }


def _matches(text: str, needle: str | None) -> bool:
    return not needle or needle.lower() in (text or "").lower()


def list_gpu_hosts(
    si: Any,
    *,
    name: str | None = None,
    vendor: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """List hosts that have at least one GPU, filtered by host name / GPU vendor. Paginated."""
    content = get_content(si)
    rows = _retrieve_props(content, vim.HostSystem, ["name", "config.graphicsInfo"])
    hosts = [_host_summary(r) for r in rows]
    hosts = [h for h in hosts if h["gpu_count"] > 0]
    hosts = [
        h
        for h in hosts
        if _matches(h["host"], name) and (not vendor or any(_matches(v, vendor) for v in h["vendors"]))
    ]
    hosts.sort(key=lambda h: h["host"])
    return envelope(hosts, limit=limit, offset=offset)


def get_gpu_host(si: Any, host_name: str) -> dict:
    """Full GPU detail for one host by name. A wrong name returns a teaching error."""
    content = get_content(si)
    rows = _retrieve_props(content, vim.HostSystem, ["name", "config.graphicsInfo"])
    for r in rows:
        if r.get("name") == host_name:
            return {
                "host": host_name,
                "gpus": _gpus_of(r),
            }
    available = sorted(r.get("name", "") for r in rows if _gpus_of(r))
    hint = f" Hosts with GPUs: {', '.join(available[:10])}." if available else ""
    raise GpuNotFoundError(
        f"Host '{host_name}' not found (or has no GPU) on this target.{hint} "
        f"Run gpu_host_list to see hosts with GPUs."
    )


def list_gpu_devices(
    si: Any,
    *,
    host: str | None = None,
    vendor: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """List physical GPU devices across hosts (flattened), filtered by host / vendor. Paginated."""
    content = get_content(si)
    rows = _retrieve_props(content, vim.HostSystem, ["name", "config.graphicsInfo"])
    devices: list[dict] = []
    for r in rows:
        host_name = r.get("name", "") or ""
        if not _matches(host_name, host):
            continue
        for gpu in _gpus_of(r):
            if not _matches(gpu["vendor"], vendor):
                continue
            devices.append({"host": host_name, **gpu})
    devices.sort(key=lambda d: (d["host"], d["pci_id"]))
    return envelope(devices, limit=limit, offset=offset)


def _vgpu_backing(device: Any) -> str:
    """The vGPU profile string on a VirtualPCIPassthrough device, or '' if not a vGPU."""
    if not isinstance(device, vim.vm.device.VirtualPCIPassthrough):
        return ""
    backing = getattr(device, "backing", None)
    return getattr(backing, "vgpu", "") or ""


def list_gpu_consumers(
    si: Any,
    *,
    profile: str | None = None,
    vm: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """List VMs consuming a vGPU (with their assigned profile), filtered by profile / VM name. Paginated."""
    content = get_content(si)
    rows = _retrieve_props(content, vim.VirtualMachine, ["name", "config.hardware.device"])
    consumers: list[dict] = []
    for r in rows:
        vm_name = r.get("name", "") or ""
        if not _matches(vm_name, vm):
            continue
        for device in r.get("config.hardware.device") or []:
            prof = _vgpu_backing(device)
            if prof and _matches(prof, profile):
                # vm name + vGPU profile are host-authored strings — sanitize before the agent sees them.
                consumers.append({"vm": _sanitize(vm_name), "profile": _sanitize(prof)})
    consumers.sort(key=lambda c: (c["profile"], c["vm"]))
    return envelope(consumers, limit=limit, offset=offset)
