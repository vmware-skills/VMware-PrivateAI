"""Regression tests for the GPU utilization vertical.

Covers the counter spec-gate (踩坑 #36), counter-id resolution, metric projection
(including the metrics-unavailable path — 踩坑 形态 #1), idle detection, and the
busiest-first / top-N ranking.
"""

from __future__ import annotations

import importlib.util
import pathlib
from types import SimpleNamespace

from pyVmomi import vim

from vmware_privateai.ops import utilization as util


def _load_spec():
    path = pathlib.Path(__file__).parent.parent / "spec" / "privateai_endpoints.py"
    spec = importlib.util.spec_from_file_location("privateai_endpoints", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- counter spec-gate ----------------------------------------------------------

def test_utilization_uses_only_verified_counters():
    spec = _load_spec()
    assert util.SPEC_COUNTERS_USED, "SPEC_COUNTERS_USED must not be empty"
    missing = util.SPEC_COUNTERS_USED - set(spec.GPU_PERF_COUNTERS)
    assert not missing, f"utilization uses counters absent from the verified index (踩坑 #36): {missing}"


# --- counter id resolution ------------------------------------------------------

def test_counter_ids_builds_group_name_rollup_key():
    counter = SimpleNamespace(
        groupInfo=SimpleNamespace(key="gpu"),
        nameInfo=SimpleNamespace(key="utilization"),
        rollupType="average",
        key=42,
    )
    assert util._counter_ids(SimpleNamespace(perfCounter=[counter])) == {"gpu.utilization.average": 42}


def test_counter_ids_tolerates_no_counters():
    assert util._counter_ids(SimpleNamespace(perfCounter=None)) == {}


# --- _query_metrics: percent scaling + sentinel filtering (review H3) -----------

def _counter(group_name, name_key, key):
    return SimpleNamespace(
        groupInfo=SimpleNamespace(key=group_name), nameInfo=SimpleNamespace(key=name_key),
        rollupType="average", key=key,
    )


def _series(counter_id, value):
    return SimpleNamespace(id=SimpleNamespace(counterId=counter_id), value=[value])


def test_query_metrics_scales_percent_and_drops_negative_sentinel(monkeypatch):
    # gpu.utilization.average=id1 (percent), gpu.mem.used.average=id2 (KB), gpu.temperature.average=id3
    pm = SimpleNamespace(
        perfCounter=[
            _counter("gpu", "utilization", 1),
            _counter("gpu", "mem.used", 2),
            _counter("gpu", "temperature", 3),
        ],
        QueryPerf=lambda **_k: [
            SimpleNamespace(
                entity=SimpleNamespace(_moId="vm-1"),
                value=[_series(1, 4523), _series(2, 8_388_608), _series(3, -1)],
            )
        ],
    )
    monkeypatch.setattr(util, "get_content", lambda si: SimpleNamespace(perfManager=pm))
    out = util._query_metrics(None, entities=[vim.VirtualMachine("vm-1", None)])
    assert out["vm-1"]["gpu_pct"] == 45.23  # 4523 basis points -> 45.23%
    assert out["vm-1"]["mem_used_kb"] == 8_388_608  # KB stays raw
    assert "temp_c" not in out["vm-1"]  # -1 sentinel dropped, not reported as a real reading


# --- projection -----------------------------------------------------------------

def _row(name, moid):
    return {"obj": SimpleNamespace(_moId=moid), "vm": name, "profile": "grid_a100-4c"}


def test_project_with_metrics_sets_idle_flag():
    out = util._project(_row("train-01", "vm-1"), {"vm-1": {"gpu_pct": 0, "mem_pct": 5, "temp_c": 40}})
    assert out["metrics_available"] is True and out["idle"] is True and out["gpu_pct"] == 0


def test_project_without_metrics_is_unavailable_not_crash():
    # 踩坑 形态 #1: a VM with no sample reads as checked-and-none, never a crash.
    out = util._project(_row("train-02", "vm-2"), {})
    assert out["metrics_available"] is False and out["idle"] is False and out["gpu_pct"] is None


# --- gpu_utilization ranking ----------------------------------------------------

def _patch(monkeypatch, rows, metrics):
    monkeypatch.setattr(util, "_gpu_vm_rows", lambda si: rows)
    monkeypatch.setattr(util, "_query_metrics", lambda si, entities: metrics)


def test_gpu_utilization_sorts_busiest_first_none_last(monkeypatch):
    rows = [_row("idle-vm", "vm-1"), _row("busy-vm", "vm-2"), _row("nodrv-vm", "vm-3")]
    metrics = {"vm-1": {"gpu_pct": 0}, "vm-2": {"gpu_pct": 88}}  # vm-3 has no sample
    _patch(monkeypatch, rows, metrics)
    out = util.gpu_utilization(None)
    order = [i["vm"] for i in out["items"]]
    assert order == ["busy-vm", "idle-vm", "nodrv-vm"]  # busiest → idle → metrics-unavailable last


def test_gpu_utilization_top_keeps_n_busiest(monkeypatch):
    rows = [_row(f"vm-{i}", f"m-{i}") for i in range(4)]
    metrics = {f"m-{i}": {"gpu_pct": i * 10} for i in range(4)}
    _patch(monkeypatch, rows, metrics)
    out = util.gpu_utilization(None, top=2)
    assert [i["gpu_pct"] for i in out["items"]] == [30, 20]


def test_gpu_utilization_filters_by_vm(monkeypatch):
    rows = [_row("train-01", "m-1"), _row("web-01", "m-2")]
    _patch(monkeypatch, rows, {"m-1": {"gpu_pct": 50}, "m-2": {"gpu_pct": 10}})
    out = util.gpu_utilization(None, vm="train")
    assert out["total"] == 1 and out["items"][0]["vm"] == "train-01"
