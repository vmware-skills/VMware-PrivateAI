---
name: vmware-privateai
description: >
  Use this skill whenever the user needs the GPU / AI-infrastructure layer of VMware Private AI
  Foundation with NVIDIA (PAIF-N) on vSphere 9.x / VCF 9.1: inventory GPU hosts and physical GPU
  devices, see which VMs consume a vGPU and the profile each holds, read real-time GPU utilization,
  list the vGPU and DirectPath profile catalog, assign a VM's vGPU profile, and list Private AI
  Service (PAIS) served models and knowledge bases. Always use this skill for "list GPU hosts",
  "which VMs are using a vGPU", "GPU utilization", "assign a vGPU profile", "list vGPU profiles",
  "list served models" when the context is explicitly VMware / vSphere / VCF Private AI / NVIDIA
  vGPU. Do NOT use for the backing VM's power/snapshot/clone/migrate (use vmware-aiops), read-only
  vSphere inventory/alarms/host health (use vmware-monitor), or GPU-enabled Tanzu Kubernetes
  (use vmware-vks). This skill is the GPU lens; vmware-aiops owns the VM lifecycle behind it.
installer:
  kind: uv
  package: vmware-privateai
allowed-tools:
  - Bash
metadata: {"openclaw":{"requires":{"env":["VMWARE_PRIVATEAI_CONFIG"],"bins":["vmware-privateai"],"config":["~/.vmware-privateai/config.yaml"]},"primaryEnv":"VMWARE_PRIVATEAI_CONFIG"}}
---

# VMware Private AI (Foundation with NVIDIA) — GPU & Model-Serving Ops

> **Disclaimer**: Community-maintained open-source project, **not affiliated with, endorsed by, or
> sponsored by VMware, Inc., Broadcom Inc., or NVIDIA Corporation.** "VMware", "vSphere", and "VCF"
> are trademarks of Broadcom; "NVIDIA" and "vGPU" are trademarks of NVIDIA. Source is publicly
> auditable under the MIT license.

The GPU / AI-infrastructure lens for the VMware skill family — GPU host & device inventory, vGPU
consumers, real-time GPU utilization, the vGPU / DirectPath profile catalog, vGPU assignment, and
**Private AI Service (PAIS)** served models and knowledge bases — over the **vSphere 9.x / VCF 9.1**
Web Services API (pyVmomi) plus the PAIS REST API.

> **Companion skills**: [vmware-aiops](https://github.com/vmware-skills/VMware-AIops) (the vCenter VMs
> behind AI workloads — power/snapshot/clone), [vmware-vks](https://github.com/vmware-skills/VMware-VKS)
> (GPU-enabled Tanzu Kubernetes), [vmware-monitor](https://github.com/vmware-skills/VMware-Monitor)
> (read-only vSphere health).

> **Status: v1.0.1 — still beta in substance.** Skill #15 of the family. The jump from 0.2.x to
> 1.0.1 is a distribution fix, not a maturity claim: the withdrawn first release used 1.0.0, and
> ClawHub resolves `latest` by version order, so every 0.x release was invisible there. The beta
> caveats below all still stand. Every API path is
> verified against official Broadcom/NVIDIA sources before use (`tests/eval/spec/privateai_endpoints.py`)
> — no endpoints written from memory. GET-response *field names* and the exact PAIS paths are
> defensive and pending validation against live 9.x hardware (see Troubleshooting). Governed by the
> family harness (audit + policy + teaching errors); read-vs-write authorization is delegated to the
> vCenter service account's RBAC role.

## What This Skill Does

| Category | Tools | Count | Read/Write |
|----------|-------|:-----:|:----------:|
| **GPU inventory** | host list/get, device list, vGPU consumer list | 4 | 4 R |
| **GPU utilization** | real-time per-vGPU-VM utilization (gpu %, mem %, temp) | 1 | 1 R |
| **GPU readiness** | per-host vGPU/PAIS readiness verdict + blocking reasons | 1 | 1 R |
| **Profile catalog** | vGPU profile list, DirectPath profile list | 2 | 2 R |
| **Profile validation** | pre-flight a vGPU profile change (power state + host offers it) | 1 | 1 R |
| **vGPU assignment** | set a VM's vGPU profile (VM must be powered off) | 1 | 1 W |
| **Private AI Service** | served-model list, model catalog, knowledge-base list, data-source list | 4 | 4 R |
| **PAIS monitoring** | fleet GPU rollup (util/mem/temp, hot/idle, busiest) | 1 | 1 R |
| **Sizing & air-gap** | LLM GPU/storage sizing advisor, local pais.yml image inspector | 2 | 2 R |

**17 MCP tools (16 read / 1 write).** Reads are strictly non-destructive. The single write
(`vgpu_assign`) previews its blast radius, refuses a powered-on VM, never powers a VM off itself, is
double-confirmed at the CLI, and is audit-logged. Pre-flight the write with `vgpu_profile_validate`.

## Quick Install

```bash
uv tool install vmware-privateai
vmware-privateai version
vmware-privateai gpu host-list        # first read — lists hosts that have a GPU
```

Config lives in `~/.vmware-privateai/config.yaml` (targets + optional `pais:` section); passwords and
the PAIS bearer token live in `~/.vmware-privateai/.env` (chmod 600). See `references/setup-guide.md`.

## When to Use This Skill

Use vmware-privateai for the **GPU / AI-infrastructure layer**: which hosts and physical devices have
GPUs, which VMs hold a vGPU and what profile, real-time GPU utilization, the assignable vGPU /
DirectPath profile catalog, changing a VM's vGPU profile, and the models / knowledge bases served by
Private AI Service — when the context is explicitly VMware / vSphere / VCF Private AI / NVIDIA vGPU.

**Do NOT use when**: the task is the backing VM's lifecycle — power on/off, snapshot, clone, migrate,
reconfigure CPU/RAM (→ **vmware-aiops**); read-only vSphere inventory, alarms, or host health
(→ **vmware-monitor**); or GPU-enabled Tanzu Kubernetes / Supervisor namespaces (→ **vmware-vks**).
`vgpu_assign` deliberately does **not** power the VM off — that is vmware-aiops's job, kept separate
so this skill's blast radius stays "one VM, when it is already off".

## Related Skills — Skill Routing

| The user wants… | Skill |
|-----------------|-------|
| Inventory GPUs / vGPU consumers / GPU utilization / assign a vGPU profile | **vmware-privateai** (this) |
| List PAIS served models / knowledge bases | **vmware-privateai** (this) |
| Power off / snapshot / clone / migrate the backing vCenter VM | vmware-aiops |
| Read-only vSphere inventory / alarms / host health | vmware-monitor |
| GPU-enabled Tanzu Kubernetes clusters / namespaces | vmware-vks |
| Multi-step GPU workflow with approval + rollback | vmware-pilot |

## Common Workflows

**1. Find an idle GPU and reassign a VM's vGPU profile.**
```
vmware-privateai gpu device-list --vendor NVIDIA     # find GPUs; vm_count 0 = idle
vmware-privateai gpu consumer-list                   # who holds a vGPU, and which profile
vmware-privateai vgpu profile-list --host esx-07     # profiles that host can hand a VM
vmware-privateai gpu vgpu-assign fin-train-01 grid_a100-4c --dry-run   # preview blast radius
# power the VM off with vmware-aiops, THEN:
vmware-privateai gpu vgpu-assign fin-train-01 grid_a100-4c             # double-confirm + audit
```
*Failure branch*: if `vgpu-assign` (confirm) refuses with "VM is powered on — a vGPU change needs the
VM powered off", run `vmware-aiops vm_power_off 'fin-train-01'` first, then re-run. If it fails with
"profile not offered by the VM's host / GPU lacks free framebuffer", run
`vmware-privateai gpu host-get <that VM's host>` to see the valid profiles and free capacity.

**2. Triage GPU utilization across the estate.**
```
vmware-privateai gpu utilization --top 10            # busiest vGPU VMs first
vmware-privateai gpu host-list --vendor NVIDIA       # which hosts carry the load
```
*Failure branch*: a VM showing `metrics unavailable (no host driver?)` is not an error — the NVIDIA
host GPU driver is not exposing counters for it (`metrics_available:false`). Deep per-SM / per-process
/ MIG-slice telemetry is **not** available via vSphere; use NVIDIA DCGM on the host for that.

**3. See what Private AI Service is serving.**
```
vmware-privateai pais model-list                     # OpenAI-compatible /models
vmware-privateai pais kb-list                         # RAG knowledge bases
```
*Failure branch*: HTTP 404 usually means a base-URL mismatch, not a bug — the `/api/v1` PAIS path
prefix is deployment-specific and unconfirmed (beta). Check `pais.endpoint` in config.yaml. HTTP
401/403 means the bearer token in `VMWARE_PRIVATEAI_PAIS_TOKEN` is expired or lacks scope — obtain a
fresh token from your Identity Provider, re-export it, and retry.

## Usage Mode

- **CLI** — interactive inventory / triage, scripting, small or local models (lower context cost).
- **MCP** — agent-driven operations with structured JSON; run `vmware-privateai mcp` (an installed
  console script, so no `uvx` network re-resolve — works through enterprise TLS proxies, 踩坑 #25).

## MCP Tools (17 — 16 read, 1 write)

| Category | Tools | R/W |
|----------|-------|:---:|
| GPU inventory | `gpu_host_list`, `gpu_host_get`, `gpu_device_list`, `gpu_consumer_list` | Read |
| GPU utilization | `gpu_utilization` | Read |
| GPU readiness | `gpu_host_readiness` | Read |
| Profile catalog | `vgpu_profile_list`, `directpath_profile_list` | Read |
| Profile validation | `vgpu_profile_validate` | Read |
| Private AI Service | `pais_model_list`, `pais_model_catalog`, `pais_knowledge_base_list`, `pais_data_source_list` | Read |
| PAIS monitoring | `pais_monitoring_summary` | Read |
| Sizing & air-gap | `pais_sizing_advise`, `pais_bundle_verify` | Read |
| vGPU assignment | `vgpu_assign` | Write |

**INFERRED PAIS paths**: `pais_model_catalog` and `pais_data_source_list` hit PAIS control-plane
paths that are unconfirmed against a live OpenAPI (踩坑 #36) — a 404 returns a base-URL teaching
message, not a bug. `pais_sizing_advise` and `pais_bundle_verify` need **no connection** (pure
computation / local file parse). `gpu_host_readiness` reports what the vSphere API exposes and says
so where it cannot (NVIDIA driver / MFT VIB / MIG need nvidia-smi on the host).

**List envelope**: every `*_list` tool returns `{items, returned, limit, offset, total, truncated, hint}`
— read rows from `items` and check `truncated` before concluding a listing is complete; empty `items`
with `truncated:false` means checked-and-none, not a failure. Lists paginate at `limit=50`; filter with
the tool's `name`/`vendor`/`host`/`profile`/`vm` arguments rather than paging the whole estate.

**Write safety (normative)**: `vgpu_assign` with `confirm=false` (the default) previews only —
current profile, target profile, power state, and that a power-off is required — without acting.
`confirm=true` applies it, but refuses a powered-on VM with a teaching error. It **never powers the VM
off itself**, waits for the real ReconfigVM task outcome (never a premature "ok"), and audits every
applied change to `~/.vmware/audit.db`.

## CLI Quick Reference

```bash
vmware-privateai gpu host-list [--name N] [--vendor V]       # hosts with a GPU
vmware-privateai gpu host-get <host>                          # full per-GPU detail
vmware-privateai gpu device-list [--host H] [--vendor V]     # physical GPUs (vm_count 0 = idle)
vmware-privateai gpu consumer-list [--profile P] [--vm V]    # VMs holding a vGPU + profile
vmware-privateai gpu utilization [--vm V] [--top N]          # real-time GPU %, mem %, temp
vmware-privateai gpu vgpu-assign <vm> <profile> [--dry-run]  # WRITE — VM must be off; double-confirm
vmware-privateai gpu readiness [--host H]                   # per-host vGPU/PAIS readiness verdict
vmware-privateai vgpu profile-list [--host H] [--model M]    # vGPU profile catalog
vmware-privateai vgpu directpath-list [--vendor V]           # DirectPath profiles (vSphere 9.0+)
vmware-privateai vgpu validate <vm> <profile>               # pre-flight a vGPU profile change (read-only)
vmware-privateai pais model-list [--name N]                  # PAIS served models
vmware-privateai pais model-catalog [--name N]               # PAIS deployable/approved model catalog
vmware-privateai pais kb-list [--name N]                     # PAIS knowledge bases
vmware-privateai pais data-source-list [--name N]            # PAIS RAG data sources
vmware-privateai pais monitoring-summary [--top N]           # fleet GPU rollup (util/mem/temp, hot/idle)
vmware-privateai pais sizing --model llama-70b               # LLM GPU/storage sizing (no connection)
vmware-privateai pais bundle-verify <pais.yml>              # local air-gap image inspector (no network)
```
Full list: `references/cli-reference.md`. Per-tool response-token estimates: `references/capabilities.md`.

## Troubleshooting

- **`Password not found for target '<t>'. Set environment variable VMWARE_PRIVATEAI_<T>_PASSWORD`** —
  add that line to `~/.vmware-privateai/.env` and `chmod 600` it, or export it (from a secret manager).
  The `<T>` is the target name upper-cased with `-`→`_`.
- **`TLS verification failed for target '<t>'`** — for a self-signed lab set `verify_ssl: false` for
  that target in `config.yaml`; otherwise install the vCenter CA on this host.
- **`gpu host-list` returns nothing on a cluster you know has GPUs** — only `shared` / `direct` /
  `sharedDirect` graphics types count as compute GPUs (the plain host framebuffer is excluded). If real
  9.x hardware surfaces a GPU under an unexpected type, that is a beta known-limitation — file an issue
  with the raw `gpu host-get` output so the projection can be widened.
- **`gpu utilization` shows a VM with `metrics unavailable`** — the NVIDIA host GPU driver is not
  exposing counters for it (not an error). Note the `gpu.*` perf counters may report at host level on
  some builds — verify the entity type on real hardware (beta caveat).
- **`directpath-list` errors with "needs vCenter 9.0+"** — DirectPathProfileManager is new in vSphere
  9.0; on 8.x use `vgpu profile-list` instead (the error routes you there, not an empty list).
- **PAIS 404 / non-JSON response** — the `/api/v1` prefix is deployment-specific and unconfirmed;
  check `pais.endpoint` (a proxy or login page returns non-JSON). PAIS 401/403 → refresh the bearer
  token in `VMWARE_PRIVATEAI_PAIS_TOKEN`.

## Audit & Safety

1. **Source Code** — https://github.com/vmware-skills/VMware-PrivateAI (MIT).
2. **Config File Contents** — `config.yaml` holds target host/username/port and the `pais.endpoint`
   only; passwords and the PAIS bearer token live in `~/.vmware-privateai/.env` (0600, obfuscated to
   `b64:` at rest — obfuscation, not encryption).
3. **Webhook Data Scope** — none. This skill makes no outbound calls except to the configured
   vCenter/ESXi targets and PAIS endpoint.
4. **TLS Verification** — on by default; `verify_ssl: false` is per-target (and `pais.verify_ssl`) and
   only for self-signed labs.
5. **Prompt Injection Protection** — all vSphere-supplied and PAIS-supplied text (device/vendor/VM/
   profile names, PAIS model ids, knowledge-base descriptions) passes through `vmware_policy.sanitize()`
   (truncation ≤500 chars + C0/C1 control-char stripping); a KB description is the highest-value
   injection surface here.
6. **Least Privilege** — read-vs-write authorization is the vCenter role's job: a read-only service
   account refuses `vgpu_assign`'s ReconfigVM at vCenter, un-bypassably. All writes are recorded in
   `~/.vmware/audit.db`. See `references/setup-guide.md`.

## License

MIT
