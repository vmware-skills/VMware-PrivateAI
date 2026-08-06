"""GPU utilization (read) — real-time per-vGPU-VM metrics via vSphere perf counters.

Counters (VERIFIED — see tests/eval/spec/privateai_endpoints.py GPU_PERF_COUNTERS):
  gpu.utilization.average (%), gpu.mem.used.average (KB),
  gpu.mem.usage.average (%), gpu.temperature.average (℃).

These require the NVIDIA host GPU driver — a VM with no samples is reported
``metrics_available: false`` (checked-and-none), never crashed (踩坑 形态 #1). Deep
per-SM / per-process / MIG-slice telemetry is NOT in vSphere (needs NVIDIA DCGM) —
listed under NO_API in the spec; this module must not invent an endpoint for it.
"""

from __future__ import annotations

from typing import Any

from pyVmomi import vim

from vmware_privateai.connection import get_content
from vmware_privateai.ops._paging import envelope
from vmware_privateai.ops._sanitize import _sanitize
from vmware_privateai.ops.gpu import _retrieve_props, _vgpu_backing

_COUNTERS = (
    "gpu.utilization.average",
    "gpu.mem.used.average",
    "gpu.mem.usage.average",
    "gpu.temperature.average",
)

# Counter spec keys this module touches — asserted ⊆ GPU_PERF_COUNTERS by the gate.
SPEC_COUNTERS_USED: frozenset[str] = frozenset(_COUNTERS)

# Agent-facing short field names.
_SHORT = {
    "gpu.utilization.average": "gpu_pct",
    "gpu.mem.used.average": "mem_used_kb",
    "gpu.mem.usage.average": "mem_pct",
    "gpu.temperature.average": "temp_c",
}

# vSphere percent perf counters return basis points (4523 = 45.23%), same as cpu.usage.average —
# scale these by 1/100 (review H3). mem_used_kb (KB) and temp_c (℃) stay raw.
_PERCENT_SHORT = frozenset({"gpu_pct", "mem_pct"})


def _gpu_vm_rows(si: Any) -> list[dict]:
    """Batched (obj, name, profile) for every VM holding a vGPU — one PropertyCollector call."""
    content = get_content(si)
    rows = _retrieve_props(content, vim.VirtualMachine, ["name", "config.hardware.device"])
    out: list[dict] = []
    for r in rows:
        for device in r.get("config.hardware.device") or []:
            profile = _vgpu_backing(device)
            if profile:
                out.append({"obj": r.get("_obj"), "vm": r.get("name", "") or "", "profile": profile})
                break
    return out


def _counter_ids(perf_manager: Any) -> dict[str, int]:
    """Map ``group.name.rollup`` -> counterId from PerformanceManager.perfCounter."""
    ids: dict[str, int] = {}
    for c in getattr(perf_manager, "perfCounter", None) or []:
        key = f"{c.groupInfo.key}.{c.nameInfo.key}.{c.rollupType}"
        ids[key] = c.key
    return ids


def _query_metrics(si: Any, entities: list) -> dict[str, dict]:
    """Real-time (20s) GPU counters for ``entities`` — {moid: {short_name: value}}.

    Returns {} when the host driver exposes no GPU counters (checked-and-none).
    """
    if not entities:
        return {}
    content = get_content(si)
    pm = content.perfManager
    id_map = _counter_ids(pm)
    wanted = {id_map[name]: _SHORT[name] for name in _COUNTERS if name in id_map}
    if not wanted:
        return {}
    metric_ids = [vim.PerformanceManager.MetricId(counterId=cid, instance="") for cid in wanted]
    specs = [
        vim.PerformanceManager.QuerySpec(entity=e, metricId=metric_ids, maxSample=1, intervalId=20)
        for e in entities
    ]
    out: dict[str, dict] = {}
    for em in pm.QueryPerf(querySpec=specs) or []:
        vals: dict[str, Any] = {}
        for series in getattr(em, "value", None) or []:
            short = wanted.get(getattr(getattr(series, "id", None), "counterId", None))
            points = getattr(series, "value", None) or []
            if not short or not points:
                continue
            value = points[-1]
            # A negative sample is vSphere's "no data" sentinel (-1), not a real reading.
            if value is None or (isinstance(value, (int, float)) and value < 0):
                continue
            if short in _PERCENT_SHORT:
                value = round(value / 100.0, 2)  # basis points -> percent (review H3)
            vals[short] = value
        out[getattr(getattr(em, "entity", None), "_moId", "")] = vals
    return out


def _project(row: dict, metrics: dict[str, dict]) -> dict:
    moid = getattr(row.get("obj"), "_moId", "")
    m = metrics.get(moid, {})
    gpu_pct = m.get("gpu_pct")
    return {
        "vm": _sanitize(row["vm"]),
        "profile": _sanitize(row["profile"]),
        "gpu_pct": gpu_pct,
        "mem_pct": m.get("mem_pct"),
        "mem_used_kb": m.get("mem_used_kb"),
        "temp_c": m.get("temp_c"),
        "metrics_available": bool(m),
        "idle": m.get("gpu_pct") == 0,
    }


def gpu_utilization(
    si: Any,
    *,
    vm: str | None = None,
    top: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Real-time GPU utilization per vGPU VM, busiest first. Filter by VM; ``top`` keeps the N busiest."""
    rows = _gpu_vm_rows(si)
    metrics = _query_metrics(si, [r["obj"] for r in rows if r.get("obj") is not None])
    items = [_project(r, metrics) for r in rows]
    if vm:
        items = [i for i in items if vm.lower() in i["vm"].lower()]
    # Busiest first; VMs with no sample sort last (None utilisation).
    items.sort(key=lambda i: (i["gpu_pct"] is None, -(i["gpu_pct"] or 0), i["vm"]))
    if top is not None and top > 0:
        items = items[:top]
    return envelope(items, limit=limit, offset=offset)
