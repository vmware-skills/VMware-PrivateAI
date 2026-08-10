# Release Notes — vmware-privateai

## v0.2.0 (beta) — 2026-08-10

Seven new **read / pre-flight** tools (10 → **17**; still 1 write), driven by the recurring PAIS /
Private AI field questions in the VCF PAIS + private-ai spaces (air-gap model/bundle delivery,
GPU-profile changes, "is my host ready", PAIS monitoring, sizing). No new destructive surface.

### New tools — 5 grounded (verified data / pure computation)

- `pais_sizing_advise` **[READ]** — GPU memory / GPU count / storage estimate for an LLM of a given
  size (pure heuristic, no connection). Honest that inference is compute/HBM-bound so random IOPS is
  the wrong axis for the weights — answers the repeated "IOPS for 27B/70B/123B" question correctly.
- `vgpu_profile_validate` **[READ]** — pre-flight for a vGPU profile change: is the VM powered off,
  does its host offer the target profile (with framebuffer)? Read-only companion to `vgpu_assign`
  (answers the escalated "change GPU profile" case, SR 36933109).
- `gpu_host_readiness` **[READ]** — per-host vGPU/PAIS readiness verdict (GPU present + `sharedDirect`
  mode + profiles offered) with `blocking_reasons`. Honest driver_note: NVIDIA driver / MFT VIB /
  MIG are not in the vSphere API (verify via nvidia-smi / esxcli).
- `pais_monitoring_summary` **[READ]** — fleet GPU rollup for VCF Ops dashboards (util/mem/temp
  avg+max, hot/idle, busiest, per-profile). Scoped honestly: deep per-SM/MIG telemetry needs DCGM.
- `pais_bundle_verify` **[READ]** — parse a LOCAL pais.yml and list the container images + registries
  to mirror for an air-gap, flagging public registries and mutable tags. No network / registry pull.

### New tools — 2 air-gap PAIS control-plane reads (INFERRED endpoints)

- `pais_model_catalog` **[READ]** — models available/approved to DEPLOY (distinct from served
  `/models`). Path `/api/v1/control/models` is INFERRED — a 404 returns a base-URL teaching message.
- `pais_data_source_list` **[READ]** — PAIS RAG data sources (ingest connectors feeding knowledge
  bases). Uses the spec-listed `/api/v1/control/data-sources` (INFERRED_EXACT).

### Notes

- The two INFERRED PAIS paths are shipped defensively, consistent with the existing PAIS reads
  (踩坑 #36): unconfirmed against a live OpenAPI, a 404 is translated to a config/base-URL hint,
  never read as a bug. Final pinning is deferred to a real PAIS deployment.
- Spec index extended (all VERIFIED): `HostSystem.config.graphicsConfig`, `VirtualMachine.runtime.host`.
- 116 regression tests (was 71); ruff clean; bandit 0 Medium+; endpoint gate now scans the new ops modules.

### Code-review hardening (pre-release)

An adversarial review caught and fixed, before shipping:
- `pais_bundle_verify` now extracts Helm-style `image: {repository, tag}` blocks and plural
  `images: [...]` lists (previously only flat `image: "ref"` strings), and an empty result is a
  **loud warning**, not a soft hint — a false "0 images to mirror" was the one dangerous outcome
  for an air-gap tool (踩坑 形态 #1).
- `vgpu_profile_validate` no longer asserts "host does not offer the profile" when the VM's
  `runtime.host` is unknown (returns `host_offers_target: null`), and reads the host name inside the
  guarded RPC so a mid-call host fault surfaces a teaching "unreachable", not an opaque mask (踩坑 #37).
- `pais_sizing_advise` surfaces `size_note` when it auto-parsed the size from a model name, and warns
  on Mixture-of-Experts `NxM` names (e.g. "Mixtral-8x7B" ≠ 7B total).
- `gpu_host_readiness` distinguishes "unreachable" from "no vGPU profiles / driver missing".

## v0.1.0 (beta) — 2026-08-06

First release. Skill #15 of the VMware skill family; the **GPU / AI-infrastructure lens** for VMware
Private AI Foundation with NVIDIA (PAIF-N) on vSphere 9.x / VCF 9.1. Independent 1.x version line (a new
skill starts at its own 0.1.0, not the family 1.8.x).

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
