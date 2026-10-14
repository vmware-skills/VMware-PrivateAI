"""assign_vgpu states its blast radius and refuses to act blind (HLD §7, revised 2026-09-16).

The tool already previewed on a bare call (L2) and refused a powered-on VM. What it did not do:

* L1 — carry a ``blast_radius`` dict on the preview and on the acting response. The
  preview listed current/target profile and power state as loose top-level keys and
  said nothing about *how* the VM would change: whether its existing vGPU device is
  edited or a second one added, and that the full memory reservation gets locked.
* L3 — refuse when the blast radius cannot be measured. A VM whose power state did
  not read went ahead (``""`` is not ``"poweredOn"``); a VM whose device list did not
  read was treated as having none, so the reconfigure *added* a vGPU next to one it
  could not see. Two VMs with the same name: the first one found was reconfigured.
* Wording — ``confirm: False previews; True applies.`` reads as an invitation.

Tests call the MCP tool through ``@vmware_tool``: what a model calling it receives.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from pyVmomi import vim
from vmware_policy import get_engine
from vmware_policy.budget import reset_budget
from vmware_policy.policy import reset_policy_engine
from vmware_policy.undo import reset_undo_store

import vmware_privateai.mcp_server.tools.gpu as gpu_tools
from vmware_privateai.mcp_server.server import mcp
from vmware_privateai.mcp_server.tools.gpu import assign_vgpu
from vmware_privateai.ops import assign


class _FakeVM:
    def __init__(self):
        self.reconfigured_with = []

    def ReconfigVM_Task(self, spec):  # noqa: N802 — mirrors the pyVmomi method name
        self.reconfigured_with.append(spec)
        return SimpleNamespace(_moId="task-1")


def _vgpu(profile: str):
    return vim.vm.device.VirtualPCIPassthrough(
        key=0, backing=vim.vm.device.VirtualPCIPassthrough.VmiopBackingInfo(vgpu=profile)
    )


def _directpath():
    return vim.vm.device.VirtualPCIPassthrough(
        key=5, backing=vim.vm.device.VirtualPCIPassthrough.DeviceBackingInfo(id="0000:3b:00.0")
    )


_MISSING = object()


def _row(name="train-01", moid="vm-42", power="poweredOff", devices=_MISSING, obj=None):
    row = {"name": name, "_moid": moid, "_obj": obj or _FakeVM()}
    if power is not None:
        row["runtime.powerState"] = power
    if devices is not None:
        row["config.hardware.device"] = [_vgpu("grid_a100-2c")] if devices is _MISSING else devices
    return row


@pytest.fixture(autouse=True)
def fresh_policy():
    reset_policy_engine()
    reset_budget()
    reset_undo_store()
    yield
    reset_policy_engine()
    reset_budget()
    reset_undo_store()


@pytest.fixture
def inventory(monkeypatch):
    """The VM rows the PropertyCollector would return; the first is the one named train-01."""
    rows = [_row(), _row(name="other-01", moid="vm-7")]
    monkeypatch.setattr(gpu_tools, "_get_connection", lambda target: object())
    monkeypatch.setattr(assign, "get_content", lambda si: object())
    monkeypatch.setattr(assign, "_retrieve_props", lambda content, obj_type, paths: rows)
    monkeypatch.setattr(assign, "_wait_for_task", lambda task: None)
    return rows


def _vm(rows):
    return rows[0]["_obj"]


def _refused(out, rows, *words):
    assert "error" in out, f"confirm=True acted: {out}"
    for word in words:
        assert word in out["error"], out["error"]
    assert _vm(rows).reconfigured_with == [], "a refused call reconfigured the VM"


# --- schema and wording ------------------------------------------------------


def _tool():
    return next(t for t in asyncio.run(mcp.list_tools()) if t.name == "assign_vgpu")


def test_schema_defaults_confirm_to_false():
    assert _tool().inputSchema["properties"]["confirm"].get("default") is False


def test_description_states_the_consequence_not_an_invitation():
    tool = _tool()
    param = tool.inputSchema["properties"]["confirm"]["description"]
    assert param.startswith("False (default) returns the blast radius and changes nothing. True applies it"), param
    assert "have not seen" in tool.description


# --- L1 / L2 -----------------------------------------------------------------


def test_a_bare_call_previews_and_reconfigures_nothing(inventory):
    out = assign_vgpu(vm_name="train-01", profile="grid_a100-4c")
    assert out["action"] == "preview" and out["applied"] is False
    assert _vm(inventory).reconfigured_with == []


def test_preview_carries_the_measured_blast_radius(inventory):
    inventory[0]["config.hardware.device"] = [_vgpu("grid_a100-2c"), _directpath()]
    out = assign_vgpu(vm_name="train-01", profile="grid_a100-4c", confirm=False)
    radius = out["blast_radius"]
    assert radius["vm"] == "train-01" and radius["vm_id"] == "vm-42"
    assert radius["current_profile"] == "grid_a100-2c" and radius["target_profile"] == "grid_a100-4c"
    assert radius["power_state"] == "poweredOff"
    assert radius["device_change"] == "edit"
    assert radius["other_passthrough_devices"] == 1, "the DirectPath device is stated as left untouched"
    assert radius["memory_reservation_locked_to_max"] is True
    assert radius["blockers"] == [] and radius["unmeasured"] == []
    assert "only after they agree" in out["hint"]
    assert "re-run with confirm=true to apply" not in out["hint"].lower()
    # The legacy top-level keys stay for one minor release.
    assert out["current_profile"] == "grid_a100-2c" and out["requires_power_off"] is True


def test_a_vm_without_a_vgpu_is_an_add(inventory):
    inventory[0]["config.hardware.device"] = []
    radius = assign_vgpu(vm_name="train-01", profile="grid_a100-4c", confirm=False)["blast_radius"]
    assert radius["device_change"] == "add" and radius["current_profile"] is None


def test_confirm_applies_once_and_states_the_blast_radius(inventory):
    out = assign_vgpu(vm_name="train-01", profile="grid_a100-4c", confirm=True)
    assert out["action"] == "applied" and out["applied"] is True
    assert out["blast_radius"]["vm_id"] == "vm-42"
    assert len(_vm(inventory).reconfigured_with) == 1


# --- L3: blockers ------------------------------------------------------------


def test_a_powered_on_vm_is_a_blocker_and_refused(inventory):
    inventory[0]["runtime.powerState"] = "poweredOn"
    preview = assign_vgpu(vm_name="train-01", profile="grid_a100-4c", confirm=False)
    assert preview["blast_radius"]["blockers"], "the preview must say it will be refused"
    _refused(assign_vgpu(vm_name="train-01", profile="grid_a100-4c", confirm=True), inventory,
             "powered on", "vmware-aiops")


def test_a_suspended_vm_is_refused(inventory):
    inventory[0]["runtime.powerState"] = "suspended"
    _refused(assign_vgpu(vm_name="train-01", profile="grid_a100-4c", confirm=True), inventory,
             "suspended", "power_off_vm")


def test_two_vms_with_the_same_name_are_refused(inventory):
    inventory.append(_row(moid="vm-99"))
    preview = assign_vgpu(vm_name="train-01", profile="grid_a100-4c", confirm=False)
    assert preview["blast_radius"]["same_name_vm_ids"] == ["vm-42", "vm-99"]
    _refused(assign_vgpu(vm_name="train-01", profile="grid_a100-4c", confirm=True), inventory,
             "2 VMs are named", "Rename")


# --- L3: unmeasured ----------------------------------------------------------


def test_an_unreadable_power_state_is_refused(inventory):
    inventory[0].pop("runtime.powerState")
    preview = assign_vgpu(vm_name="train-01", profile="grid_a100-4c", confirm=False)
    assert preview["blast_radius"]["unmeasured"] == ["power state"]
    _refused(assign_vgpu(vm_name="train-01", profile="grid_a100-4c", confirm=True), inventory,
             "power state", "Nothing was changed")


def test_an_unreadable_device_list_is_refused_not_read_as_no_vgpu(inventory):
    inventory[0].pop("config.hardware.device")
    preview = assign_vgpu(vm_name="train-01", profile="grid_a100-4c", confirm=False)
    radius = preview["blast_radius"]
    assert radius["unmeasured"] == ["virtual devices"]
    assert radius["device_change"] is None, "unknown devices must not be reported as an add"
    _refused(assign_vgpu(vm_name="train-01", profile="grid_a100-4c", confirm=True), inventory,
             "virtual devices")


def test_a_refusal_is_audited_as_a_failure(inventory):
    inventory[0]["runtime.powerState"] = "poweredOn"
    out = assign_vgpu(vm_name="train-01", profile="grid_a100-4c", confirm=True)
    assert "error" in out
    db = Path(get_engine()._path)
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as con:
        status, result = con.execute(
            "SELECT status, result FROM audit_log WHERE tool = 'assign_vgpu' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert status == "error", f"the refused call was audited as {status!r}"
    assert "powered on" in result
