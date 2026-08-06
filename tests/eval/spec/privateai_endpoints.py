"""Verified API surface for vmware-privateai — the anti-phantom-endpoint gate.

Every runtime access path (pyVmomi object path, perf counter, or PAIS REST
endpoint) the code touches MUST appear here, and every entry here was verified
against an official Broadcom source on 2026-08-06 (踩坑 #36: a prior skill
shipped API paths written from model memory and half 404'd). Regression tests
assert that ops code only reaches paths in this index.

Status legend:
  VERIFIED         — confirmed against primary docs / govmomi / pyVmomi API ref
  INFERRED_EXACT   — capability/API confirmed to exist, but the exact path string
                     must be re-pulled from the live OpenAPI before being pinned
  NO_API           — capability is real but has NO usable programmatic API
                     (CLI/UI only) — code must NOT invent an endpoint for it
"""

from __future__ import annotations

# --- vSphere Web Services API (pyVmomi) — GPU inventory / profiles / consumers / assignment ---
# All VERIFIED (vim.host.GraphicsInfo API ref, govmomi vim25/mo, William Lam vSphere 9.0 vGPU/DirectPath).
PYVMOMI_OBJECTS: dict[str, dict[str, object]] = {
    "gpu_host_graphics_info": {
        "path": "HostSystem.config.graphicsInfo",
        "type": "vim.host.GraphicsInfo[]",
        "fields": ["deviceName", "graphicsType", "memorySizeInKB", "pciId", "vendorName", "vm"],
        "status": "VERIFIED",
    },
    "gpu_host_graphics_manager": {
        "path": "HostSystem.configManager.graphicsManager",
        "type": "HostGraphicsManager",
        "fields": ["graphicsInfo", "graphicsConfig", "sharedPassthruGpuTypes", "sharedGpuCapabilities"],
        "status": "VERIFIED",
    },
    "gpu_pci_passthru": {
        "path": "HostSystem.configManager.pciPassthruSystem",
        "type": "HostPciPassthruSystem",
        "fields": ["pciPassthruInfo.passthruActive", "pciPassthruInfo.passthruEnabled", "pciPassthruInfo.id"],
        "status": "VERIFIED",
    },
    "gpu_pci_device": {
        "path": "HostSystem.hardware.pciDevice",
        "type": "vim.host.PciDevice[]",
        "fields": ["vendorId", "deviceId", "deviceName", "id"],
        "status": "VERIFIED",
    },
    "vgpu_profile_catalog": {
        "path": "EnvironmentBrowser.QueryConfigTarget(host)",
        # CORRECTED 2026-08-06 against installed pyVmomi 9.1.0.0 introspection: the
        # ConfigTarget attribute is `vgpuProfileInfo`, NOT `vgpu` (the spec doc's
        # `vgpu[]` does not exist on vim.vm.ConfigTarget — a phantom, 踩坑 #36), and
        # VirtualMachineVgpuProfileInfo carries profileName/name/fbSizeInGib, NOT
        # profile/model/framebuffer. Code reads vgpuProfileInfo with a `vgpu` fallback.
        "type": "vim.vm.ConfigTarget.vgpuProfileInfo[] (VirtualMachineVgpuProfileInfo)",
        "fields": ["profileName", "name", "fbSizeInGib", "profileClass", "profileSharing", "deviceVendorId"],
        "attr_fallback": "vgpu",
        "status": "VERIFIED",
    },
    "directpath_profiles": {
        "path": "DirectPathProfileManager.ListDirectPathProfiles",
        # VERIFIED 2026-08-06 against pyVmomi 9.1.0.0: content.directPathProfileManager
        # (vCenter-level MO, NEW in vSphere 9.0) exposes ListDirectPathProfiles(filterSpec)
        # -> DirectPathProfileInfo[] (id/name/vendorName/description). Capacity via
        # QueryDirectPathProfileCapacity(target, querySpec) needs a TargetEntity — DEFERRED
        # (requires real-hw target context; not exercised by the read tool).
        "type": "content.directPathProfileManager -> DirectPathProfileInfo[]",
        "fields": ["id", "name", "vendorName", "description"],
        "status": "VERIFIED",
    },
    "vm_vgpu_consumer": {
        "path": "VirtualMachine.config.hardware.device[] -> VirtualPCIPassthrough.backing (VmiopBackingInfo)",
        "type": "vim.vm.device.VirtualPCIPassthrough",
        "fields": ["backing.vgpu", "deviceInfo.label", "deviceInfo.summary"],
        "status": "VERIFIED",
    },
    "vm_power_state": {
        # VERIFIED (vim.VirtualMachine.runtime.powerState — VirtualMachineRuntimeInfo). The
        # assignment op reads this to refuse a vGPU change on a powered-on VM. It was used by
        # the code before being listed here — the strengthened path gate (review M1) surfaced it.
        "path": "VirtualMachine.runtime.powerState",
        "type": "vim.VirtualMachinePowerState",
        "fields": ["poweredOn", "poweredOff", "suspended"],
        "status": "VERIFIED",
    },
    "vm_vgpu_assign": {
        "path": "VirtualMachine.ReconfigVM_Task(deviceChange add VirtualPCIPassthrough(VmiopBackingInfo(vgpu=...)))",
        "type": "write",
        "fields": ["memoryReservationLockedToMax=True"],
        "write_requires": "VM powered OFF (no hot-add, no vMotion for passthrough)",
        "status": "VERIFIED",
    },
}

# --- Performance counters (vim PerformanceManager / QueryPerf) — GPU utilization ---
# VERIFIED (virten.net vSphere 8.0 perf counter list; carried into 9.x). Require NVIDIA host driver.
GPU_PERF_COUNTERS: dict[str, str] = {
    "gpu.utilization.average": "percent",
    "gpu.mem.used.average": "kilobytes",
    "gpu.mem.usage.average": "percent",
    "gpu.temperature.average": "celsius",
}

# --- Private AI Service (PAIS) REST API — model serving / knowledge bases / vector indexes ---
# API EXISTS and is public (developer.broadcom.com/xapis/vmware-private-ai-service-api).
# RE-VERIFIED 2026-08-06 via a WebFetch of the RENDERED developer portal
# (developer.broadcom.com/xapis/vmware-private-ai-service-api/latest/): it corroborates
# the `/api/v1` root prefix, the two path categories (/compatibility/openai/v1/... and
# /control/...), and OIDC/OAuth2 bearer auth (Authorization: Bearer <access_token>,
# Authorization-Code-with-PKCE). The verified-endpoints source doc (section A) lists these
# same paths WITHOUT the prefix and marks them INFERRED.
# STATUS = INFERRED_EXACT (deliberately NOT VERIFIED): the corroboration is a rendered-page
# read, NOT a downloaded OpenAPI JSON and NOT a live deployment. The exact `/api/v1` prefix
# is therefore treated as unconfirmed and its final pinning is DEFERRED to the first live
# run against a real PAIS endpoint. The base URL (scheme/host/namespace, and whether the
# `/api/v1` prefix belongs to base_url vs. path) is operator-configured, so a 404 should
# prompt a config check before being treated as a bug (踩坑 #36). Paths below are absolute
# (the client joins the operator-configured base_url in front).
PAIS_ENDPOINTS: dict[str, dict[str, str]] = {
    "list_models": {"method": "GET", "path": "/api/v1/compatibility/openai/v1/models", "status": "INFERRED_EXACT"},
    "list_data_sources": {"method": "GET", "path": "/api/v1/control/data-sources", "status": "INFERRED_EXACT"},
    "list_knowledge_bases": {"method": "GET", "path": "/api/v1/control/knowledge-bases", "status": "INFERRED_EXACT"},
    "list_indexes": {
        "method": "GET",
        "path": "/api/v1/control/knowledge-bases/{kb}/indexes",
        "status": "INFERRED_EXACT",
    },
}

# --- Capabilities with NO usable programmatic API — code must NOT invent endpoints for these ---
NO_API: dict[str, str] = {
    "mig_mode_set": "nvidia-smi -mig on ESXi host + reboot; no vSphere API",
    "gpu_driver_version": "nvidia-smi / esxcli software vib list on host; not in vSphere API",
    "deep_gpu_telemetry": "per-SM / per-process / MIG-slice / power via NVIDIA DCGM; not in vCenter perf counters",
    "dl_vm_one_call_deploy": "no single deploy API — Content Library OVF deploy OR Supervisor VM CRD OR VCF Automation catalog",
    "pgvector": "plain PostgreSQL 16.8 + pgvector behind VMware Data Services Manager (DSM) — separate API",
}

# Relative pyVmomi property paths (as passed to a PropertyCollector ``pathSet``) that the
# ops layer is allowed to retrieve. These are the object-relative fragments the code actually
# hands to _retrieve_props — anchored back to PYVMOMI_OBJECTS by the gate, and asserted to be a
# superset of the AST-scanned real call sites (review M1: the old gate only checked a hand-
# declared SPEC_KEYS_USED and never the literal pathSet strings). ``name`` is the universal
# managed-object property. QueryConfigTarget / ReconfigVM are method calls, not property paths.
VERIFIED_PROPERTY_PATHS: frozenset[str] = frozenset(
    {
        "name",
        "config.graphicsInfo",  # gpu_host_graphics_info
        "config.hardware.device",  # vm_vgpu_consumer
        "runtime.powerState",  # vm_power_state
    }
)

# Every path/counter/endpoint string the ops layer is allowed to touch at runtime.
ALLOWED_PATHS: frozenset[str] = frozenset(
    [str(v["path"]) for v in PYVMOMI_OBJECTS.values()]
    + list(GPU_PERF_COUNTERS)
    + [e["path"] for e in PAIS_ENDPOINTS.values()]
)
