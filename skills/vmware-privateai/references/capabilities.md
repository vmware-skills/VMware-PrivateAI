# vmware-privateai — Capabilities

17 MCP tools (16 read / 1 write) over the vSphere 9.x / VCF 9.1 Web Services API (pyVmomi) plus the
Private AI Service (PAIS) REST API, with two tools that need no connection at all (sizing / bundle).
Every vSphere tool accepts an optional `target`; PAIS tools use the `pais:` config section instead.
Typical response tokens are estimates for a small estate; every `*_list` tool paginates at `limit=50`
and returns the `{items, returned, limit, offset, total, truncated, hint}` envelope.

## GPU inventory (4 read)
| Tool | R/W | Returns | ~tokens |
|------|:---:|---------|:------:|
| `gpu_host_list` | R | host, gpu_count, vendors[], vgpu_vms (filter name/vendor) | 60–400 |
| `gpu_host_get` | R | one host's GPUs: device, type, vendor, memory_mb, pci_id, vm_count | 80–400 |
| `gpu_device_list` | R | flattened physical GPUs: host, device, type, vendor, pci_id, memory_mb, vm_count (filter host/vendor) | 80–600 |
| `gpu_consumer_list` | R | vm, profile — the "who holds a vGPU" view (filter profile/vm) | 60–500 |

## GPU utilization (1 read)
| Tool | R/W | Returns | ~tokens |
|------|:---:|---------|:------:|
| `gpu_utilization` | R | vm, profile, gpu_pct, mem_pct, mem_used_kb, temp_c, metrics_available, idle; busiest first, `top` keeps N | 80–500 |

Real-time 20s samples via the vSphere PerformanceManager `gpu.*` counters (require the NVIDIA host GPU
driver). A negative sample is vSphere's "no data" sentinel and is dropped; a VM with no samples reports
`metrics_available:false`. **Beta caveat**: the `gpu.*` counters may report at host level on some
builds — verify the entity type on real hardware. Deep per-SM / per-process / MIG-slice telemetry is
**not** in vSphere (needs NVIDIA DCGM) and no endpoint is invented for it.

## GPU readiness (1 read)
| Tool | R/W | Returns | ~tokens |
|------|:---:|---------|:------:|
| `gpu_host_readiness` | R | host, vgpu_ready, gpu_count, vendors[], total_gpu_memory_mb, default_graphics_type, vgpu_profiles_offered, active_vgpu_vms, blocking_reasons[], driver_note (filter/scope host) | 100–600 |

Combines `config.graphicsInfo` + `config.graphicsConfig` + the per-host `QueryConfigTarget` profile
catalog into a `vgpu_ready` verdict (GPU present + `sharedDirect` mode + ≥1 profile offered). Only
GPU hosts are returned; a per-host query failure lands in `unreachable_hosts`. The NVIDIA driver /
MFT VIB version and MIG geometry are **not** in the vSphere API — every item carries a `driver_note`
routing to `nvidia-smi` / `esxcli` (spec NO_API; no endpoint invented).

## Profile catalog (2 read)
| Tool | R/W | Returns | ~tokens |
|------|:---:|---------|:------:|
| `vgpu_profile_list` | R | profile, name, framebuffer_gib, profile_class, sharing, vendor_id, hosts[], host_count, unreachable_hosts[] (filter/scope host, filter model) | 80–600 |
| `directpath_profile_list` | R | id, name, vendor, description — vCenter-level DirectPath profiles, **vSphere 9.0+** (filter name/vendor) | 60–400 |

`vgpu_profile_list` polls `EnvironmentBrowser.QueryConfigTarget` **per host** — scope with `host` to
avoid polling the whole estate. A host whose per-host query fails lands in `unreachable_hosts` (a
reachability problem, not "no profiles"). `directpath_profile_list` on a pre-9.0 vCenter raises a
teaching error routing to `vgpu_profile_list` rather than returning an empty list.

## Profile validation (1 read)
| Tool | R/W | Returns | ~tokens |
|------|:---:|---------|:------:|
| `vgpu_profile_validate` | R | vm, host, current_profile, target_profile, power_state, host_offers_target, target_framebuffer_gib, can_apply, blocking_reasons[] | 80–300 |

Read-only pre-flight for `vgpu_assign`: checks the two ReconfigVM failure modes up front — VM powered
on, or the VM's own host does not offer the target profile. No reconfigure; run `vgpu_assign` once
`can_apply` is true.

## Private AI Service (4 read)
| Tool | R/W | Returns | ~tokens |
|------|:---:|---------|:------:|
| `pais_model_list` | R | id, owned_by, created — OpenAI-compatible served `/models` (filter name) | 60–400 |
| `pais_model_catalog` | R | id, name, status, source — deployable/approved model catalog (filter name) | 60–500 |
| `pais_knowledge_base_list` | R | id, name, status, description — RAG knowledge bases (filter name) | 80–500 |
| `pais_data_source_list` | R | id, name, type, status — RAG ingest connectors (filter name) | 60–400 |

PAIS reads go through the bearer-authenticated REST client (`VMWARE_PRIVATEAI_PAIS_TOKEN`). Responses
are parsed defensively: a bare JSON array or a `{data|items|models|knowledge_bases|…: [...]}` envelope is
accepted, and every field degrades via `.get()`. **Beta caveat**: the exact `/api/v1` path prefix and
the JSON field names are `INFERRED_EXACT` (corroborated by the rendered Broadcom developer portal, not a
downloaded OpenAPI or a live deployment) — a 404 usually means a base-URL mismatch, not a bug.
`pais_model_catalog` (`/api/v1/control/models`) is the least-confirmed of these (a best-guess path).

## PAIS monitoring (1 read)
| Tool | R/W | Returns | ~tokens |
|------|:---:|---------|:------:|
| `pais_monitoring_summary` | R | vgpu_vms, reporting_vms, idle_vms, hot_vms, gpu_pct avg/max, mem_pct avg/max, temp_c_max, by_profile{}, busiest[], scope_note | 150–500 |

A fleet rollup of the same VERIFIED `gpu.*` perf counters `gpu_utilization` reads — the numbers you
would pin to a VCF Ops dashboard. Uses the vCenter connection (not PAIS REST). Deep per-SM / MIG /
power telemetry needs NVIDIA DCGM and is out of scope (`scope_note` says so).

## Sizing & air-gap (2 read — no connection)
| Tool | R/W | Returns | ~tokens |
|------|:---:|---------|:------:|
| `pais_sizing_advise` | R | model_billions, precision, weights_gib, serving_vram_gib, gpu_options[], storage{}, assumptions{} | 200–400 |
| `pais_bundle_verify` | R | manifest, image_count, images[], registries_to_mirror[], public_registries[], mutable_images[], warnings[] | 150–800 |

Both are pure/local — **no vCenter or PAIS call**. `pais_sizing_advise` is a transparent first-
principles heuristic (weights = params × bytes/precision; GPU count = ceil(serving / usable HBM)) and is
explicit that LLM inference is compute/HBM-bound, so random IOPS is the wrong axis for the weights.
`pais_bundle_verify` parses a **local** pais.yml with a real YAML parser (踩坑 #38) and flags the two
air-gap blockers (public registries to mirror, mutable tags); it never contacts a registry.

## vGPU assignment (1 write)
| Tool | R/W | Risk | Blast radius |
|------|:---:|:----:|--------------|
| `vgpu_assign` | W | high | one VM, **only when powered off** — sets/replaces its vGPU profile via ReconfigVM |

`confirm=false` (default) previews (current→target profile, power_state, requires_power_off, applied:false)
without acting. `confirm=true` applies it but refuses a powered-on VM with a teaching error. It edits the
VM's **existing vGPU device** (selected by the same predicate the preview uses, never a plain
DirectPath/SR-IOV passthrough), waits for the real ReconfigVM task outcome (never a premature "ok"), and
audits the result. It never powers the VM off itself — that is `vmware-aiops`.

## Verified API surface (anti-phantom-endpoint gate)

Every runtime path is pinned in `tests/eval/spec/privateai_endpoints.py` and asserted by a regression
gate (踩坑 #36):

- **pyVmomi**: `HostSystem.config.graphicsInfo` (VERIFIED), `HostSystem.config.graphicsConfig`
  (VERIFIED — vGPU mode via `hostDefaultGraphicsType`), `EnvironmentBrowser.QueryConfigTarget →
  ConfigTarget.vgpuProfileInfo[]` (VERIFIED — the attribute is `vgpuProfileInfo`, not the spec-doc's
  phantom `vgpu[]`), `content.directPathProfileManager.ListDirectPathProfiles` (VERIFIED, 9.0+),
  `VirtualMachine.config.hardware.device → VirtualPCIPassthrough.backing.vgpu` (VERIFIED),
  `VirtualMachine.runtime.powerState` (VERIFIED), `VirtualMachine.runtime.host` (VERIFIED),
  `ReconfigVM_Task` (VERIFIED, requires VM off).
- **Perf counters**: `gpu.utilization.average`, `gpu.mem.used.average`, `gpu.mem.usage.average`,
  `gpu.temperature.average` (VERIFIED).
- **PAIS REST**: `/api/v1/compatibility/openai/v1/models`, `/api/v1/control/knowledge-bases`,
  `/api/v1/control/data-sources`, `/api/v1/control/models` (INFERRED_EXACT — path prefix deferred to
  first live run; `/control/models` is a best-guess pending a live OpenAPI).
- **NO_API** (code must not invent an endpoint): MIG mode set, GPU driver version, deep GPU telemetry
  (DCGM), one-call DL-VM deploy, pgvector.
