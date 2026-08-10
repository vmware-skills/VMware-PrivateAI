"""Regression tests for pais_monitoring_summary (GPU rollup over verified perf counters).

Covers the aggregation math (counts, avg/max, hot/idle), the busiest list, per-profile tally,
the non-reporting-driver case (踩坑 形态 #1: vGPU VMs but zero counters = checked-and-none),
and the honest DCGM scope note.
"""

from __future__ import annotations

from types import SimpleNamespace

from vmware_privateai.ops import monitoring


def _row(moid: str, vm: str, profile: str):
    return {"obj": SimpleNamespace(_moId=moid), "vm": vm, "profile": profile}


def _fake(monkeypatch, rows, metrics):
    monkeypatch.setattr(monitoring, "_gpu_vm_rows", lambda si: rows)
    monkeypatch.setattr(monitoring, "_query_metrics", lambda si, entities: metrics)


def test_rollup_counts_and_stats(monkeypatch):
    rows = [_row("vm-1", "a", "a100-4c"), _row("vm-2", "b", "a100-4c"), _row("vm-3", "c", "h100-8c")]
    metrics = {
        "vm-1": {"gpu_pct": 95.0, "mem_pct": 80.0, "mem_used_kb": 100, "temp_c": 72.0},
        "vm-2": {"gpu_pct": 0.0, "mem_pct": 5.0, "mem_used_kb": 1, "temp_c": 40.0},
        # vm-3 has no metrics -> non-reporting
    }
    _fake(monkeypatch, rows, metrics)
    out = monitoring.pais_monitoring_summary(None, hot_pct=90.0, top=2)
    assert out["vgpu_vms"] == 3
    assert out["reporting_vms"] == 2 and out["non_reporting_vms"] == 1
    assert out["hot_vms"] == 1 and out["idle_vms"] == 1
    assert out["gpu_pct_max"] == 95.0 and out["gpu_pct_avg"] == 47.5
    assert out["temp_c_max"] == 72.0
    assert out["by_profile"] == {"a100-4c": 2, "h100-8c": 1}
    assert out["busiest"][0]["vm"] == "a" and out["busiest"][0]["gpu_pct"] == 95.0
    assert "DCGM" in out["scope_note"]


def test_vgpu_vms_but_none_reporting_hints_driver(monkeypatch):
    # 踩坑 形态 #1: vGPU VMs present but zero counters = driver not exposing, not an error.
    _fake(monkeypatch, [_row("vm-1", "a", "a100-4c")], {})
    out = monitoring.pais_monitoring_summary(None)
    assert out["vgpu_vms"] == 1 and out["reporting_vms"] == 0
    assert out["gpu_pct_avg"] is None
    assert "driver" in out["hint"].lower()


def test_empty_estate_is_clean(monkeypatch):
    _fake(monkeypatch, [], {})
    out = monitoring.pais_monitoring_summary(None)
    assert out["vgpu_vms"] == 0 and out["reporting_vms"] == 0 and out["hint"] == ""


def test_hot_threshold_is_configurable(monkeypatch):
    rows = [_row("vm-1", "a", "p")]
    _fake(monkeypatch, rows, {"vm-1": {"gpu_pct": 75.0, "mem_pct": 50.0, "mem_used_kb": 1, "temp_c": 60.0}})
    assert monitoring.pais_monitoring_summary(None, hot_pct=90.0)["hot_vms"] == 0
    assert monitoring.pais_monitoring_summary(None, hot_pct=70.0)["hot_vms"] == 1
