"""PAIS GPU monitoring rollup (read) — a fleet-level GPU health summary for VCF Ops dashboards.

Field ask (Shivani Manro, VCF PAIS): "how do I monitor PAIS in VCF Operations 9.1 — dashboards,
key metrics, alerts?". This op aggregates the per-VM vGPU counters (which gpu_utilization already
reads from VERIFIED vSphere perf counters) into a single estate rollup: how many vGPU VMs, how
many are actually reporting, average/peak GPU + memory utilisation, hottest GPU, idle vs. hot
VMs, and the busiest N. It is the data an operator would pin to a dashboard or alert on.

Scope is honestly bounded (踩坑 #36 / spec NO_API): vSphere exposes VM-level gpu.* counters only.
Per-SM / per-process / MIG-slice / power telemetry needs NVIDIA DCGM and is NOT available here —
the summary says so rather than implying full observability.
"""

from __future__ import annotations

from typing import Any

from vmware_privateai.ops.utilization import _gpu_vm_rows, _project, _query_metrics

# The gpu_pct at/above which a vGPU VM is called "hot" (saturated) in the rollup.
_HOT_PCT = 90.0

_DCGM_NOTE = (
    "VM-level gpu.* counters only. Per-SM / per-process / MIG-slice / power telemetry needs NVIDIA "
    "DCGM and is not in vSphere — pair with the NVIDIA vGPU Management Pack for full observability."
)


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def pais_monitoring_summary(si: Any, *, hot_pct: float = _HOT_PCT, top: int = 5) -> dict:
    """Aggregate real-time vGPU utilisation across the estate into one monitoring rollup.

    Returns counts (total vGPU VMs, reporting, idle, hot), average/peak GPU and memory
    utilisation, the hottest temperature, a per-profile tally, and the busiest ``top`` VMs.
    An estate with vGPU VMs but zero reporting means the NVIDIA host driver is not exposing
    counters (checked-and-none), not an error.
    """
    rows = _gpu_vm_rows(si)
    metrics = _query_metrics(si, [r["obj"] for r in rows if r.get("obj") is not None])
    vms = [_project(r, metrics) for r in rows]

    reporting = [v for v in vms if v["metrics_available"]]
    gpu_vals = [v["gpu_pct"] for v in reporting if isinstance(v["gpu_pct"], (int, float))]
    mem_vals = [v["mem_pct"] for v in reporting if isinstance(v["mem_pct"], (int, float))]
    temp_vals = [v["temp_c"] for v in reporting if isinstance(v["temp_c"], (int, float))]

    hot = [v for v in reporting if isinstance(v["gpu_pct"], (int, float)) and v["gpu_pct"] >= hot_pct]
    idle = [v for v in reporting if v["gpu_pct"] == 0]

    by_profile: dict[str, int] = {}
    for v in vms:
        by_profile[v["profile"]] = by_profile.get(v["profile"], 0) + 1

    busiest = sorted(reporting, key=lambda v: (-(v["gpu_pct"] or 0), v["vm"]))[: max(0, top)]

    return {
        "vgpu_vms": len(vms),
        "reporting_vms": len(reporting),
        "non_reporting_vms": len(vms) - len(reporting),
        "idle_vms": len(idle),
        "hot_vms": len(hot),
        "hot_threshold_pct": hot_pct,
        "gpu_pct_avg": _avg(gpu_vals),
        "gpu_pct_max": max(gpu_vals) if gpu_vals else None,
        "mem_pct_avg": _avg(mem_vals),
        "mem_pct_max": max(mem_vals) if mem_vals else None,
        "temp_c_max": max(temp_vals) if temp_vals else None,
        "by_profile": dict(sorted(by_profile.items())),
        "busiest": [
            {
                "vm": v["vm"],
                "profile": v["profile"],
                "gpu_pct": v["gpu_pct"],
                "mem_pct": v["mem_pct"],
                "temp_c": v["temp_c"],
            }
            for v in busiest
        ],
        "scope_note": _DCGM_NOTE,
        "hint": (
            "No vGPU VM is reporting GPU counters — confirm the NVIDIA host driver is installed "
            "(gpu_host_readiness) and VMs are powered on."
            if vms and not reporting
            else ""
        ),
    }
