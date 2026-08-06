# Release Notes — vmware-privateai

## v1.0.0 (beta) — 2026-08-06

First release. Skill #15 of the VMware skill family; the **GPU / AI-infrastructure lens** for VMware
Private AI Foundation with NVIDIA (PAIF-N) on vSphere 9.x / VCF 9.1. Independent 1.x version line (a new
skill starts at its own 1.0.0, not the family 1.8.x).

### MCP tools — 10 (9 read, 1 write)

GPU inventory (read):
- `gpu_host_list` — ESXi hosts that have at least one GPU (filter name / vendor).
- `gpu_host_get` — full per-GPU detail for one host (device, type, memory, consumers).
- `gpu_device_list` — flattened physical GPU devices across hosts (`vm_count 0` = idle).
- `gpu_consumer_list` — VMs consuming a vGPU and the profile each holds.

GPU utilization (read):
- `gpu_utilization` — real-time per-vGPU-VM utilization (gpu %, mem %, mem_used_kb, temp), busiest first.

Profile catalog (read):
- `vgpu_profile_list` — the vGPU profile catalog aggregated across hosts (framebuffer, class, sharing).
- `directpath_profile_list` — vCenter-level DirectPath (dynamic passthrough) profiles, vSphere 9.0+.

Private AI Service (read):
- `pais_model_list` — models served by PAIS (OpenAI-compatible `/models`).
- `pais_knowledge_base_list` — PAIS knowledge bases (RAG vector stores).

vGPU assignment (write):
- `vgpu_assign` — set/replace a VM's vGPU profile via ReconfigVM. Previews by default; requires the VM
  powered off; never powers it off itself; double-confirmed at the CLI; audited.

A matching CLI ships for all ten (`gpu`, `vgpu`, `pais` command groups) plus `version` and `mcp`.

### Governed-ops harness

- Every tool is wrapped by `vmware-policy`'s `@vmware_tool` (MCP) / `@guarded` (CLI write) — pre-checks,
  policy engine, audit to `~/.vmware/audit.db`, teaching-error formatting.
- All vSphere- and PAIS-supplied text passes through `vmware_policy.sanitize()` (≤500-char truncation +
  C0/C1 control-char stripping) before reaching the model.
- REST error translation is centralized in the PAIS client's `_request` (踩坑 #37); vSphere connection
  errors become teaching `ConfigError` / `ConnectionError` messages, not tracebacks.
- Reads paginate at 50 with a `{items, returned, limit, offset, total, truncated, hint}` envelope
  (empty items + `truncated:false` = checked-and-none, never read as "no problem").
- **Anti-phantom-endpoint gate**: every runtime pyVmomi path, perf counter, and PAIS REST path is pinned
  in `tests/eval/spec/privateai_endpoints.py` and asserted by a regression test — no endpoint written
  from model memory (踩坑 #36). MCP server lives under `vmware_privateai.mcp_server` (namespaced, never a
  top-level `mcp_server` — 踩坑 #41); tool signatures use `Optional[X]`, not PEP 604 (踩坑 #33).

### Beta activation caveats (honest, pending first real-hardware run)

This release is functionally complete and import-clean, with regression coverage over its error paths,
but has **not yet been exercised against live 9.x GPU hardware**. Before relying on it in production,
run the read tools once against a real target and confirm:

- **GET-response field names are INFERRED / defensive.** vGPU-profile, GPU-device, and consumer
  projections read every field with `getattr` / `.get()` and degrade an absent field to empty rather than
  crashing. A field that a live build names differently will surface as a blank column, not an error —
  file an issue with the raw `gpu host-get` / `vgpu profile-list` output so the projection can be pinned.
- **PAIS exact paths are INFERRED_EXACT.** The `/api/v1` prefix and the JSON response shape are
  corroborated by the rendered Broadcom developer portal, **not** by a downloaded OpenAPI JSON or a live
  deployment. A 404 usually means a base-URL mismatch (check `pais.endpoint`), not a bug; the response
  parser accepts both a bare array and a `{data|items|models|knowledge_bases|…}` envelope.
- **`gpu.*` perf counters may be host-level on some builds.** The four `gpu.*` counters are verified to
  exist, but whether they report per-VM or per-host on a given ESXi build must be confirmed on real
  hardware — verify the entity type before reading per-VM utilization as authoritative.
- **MIG has no vSphere API.** MIG mode set, GPU driver version, and deep per-SM / per-process / MIG-slice
  telemetry are **not** available through vCenter (use `nvidia-smi` / NVIDIA DCGM on the host). The code
  lists these under `NO_API` and deliberately invents no endpoint for them.

### Security

- Passwords and the PAIS bearer token live only in `~/.vmware-privateai/.env` (chmod 600, `b64:`
  obfuscated at rest). Read-vs-write authorization is delegated to the vCenter RBAC role.
- `bandit -r vmware_privateai/`: target 0 Medium+ issues. See `SECURITY.md`.
