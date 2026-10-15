<!-- mcp-name: io.github.vmware-skills/vmware-privateai -->

# VMware Private AI (Foundation with NVIDIA) — GPU & Model-Serving Ops

The **GPU / AI-infrastructure** lens of the VMware skill family — GPU host and device inventory, vGPU consumers,
live GPU utilization, the vGPU / DirectPath profile catalog, vGPU assignment, and the models and knowledge bases
served by **Private AI Service (PAIS)** — over the **vSphere 9.x / VCF 9.1** Web Services API (pyVmomi) and the
PAIS REST API. MCP server + CLI.

> **Disclaimer**: This is a community-maintained open-source project and is **not affiliated with,
> endorsed by, or sponsored by VMware, Inc., Broadcom Inc., or NVIDIA Corporation.** "VMware", "vSphere",
> and "VCF" are trademarks of Broadcom; "NVIDIA" and "vGPU" are trademarks of NVIDIA. Source is auditable at
> [github.com/vmware-skills/VMware-PrivateAI](https://github.com/vmware-skills/VMware-PrivateAI) under the MIT license.

> **Status: beta.** Skill #15 of the family, on its own version line. Every API path is verified against official
> Broadcom/NVIDIA docs before use (see `tests/eval/spec/privateai_endpoints.py`) — no endpoints written from memory.
> GET response **field names** and the exact PAIS paths are defensive and await validation on real 9.x hardware
> (see the beta caveats in `skills/vmware-privateai/SKILL.md`). Governed by the family harness (audit + policy +
> teaching errors); read/write authorization is left to the RBAC role of the vCenter service account.

## Upgrading to 2.0

2.0 renames all 17 MCP tools so every name starts with a verb (e.g. `directpath_profile_list` → `list_directpath_profiles`, `gpu_consumer_list` → `list_gpu_consumers`, `gpu_device_list` → `list_gpu_devices`). The full old → new table is in
[RELEASE_NOTES.md](RELEASE_NOTES.md#v200--mcp-tools-renamed-verb-first). Nothing else changes: same tools, parameters,
safety gates and CLI commands. If an agent prompt, script or `~/.vmware/rules.yaml` names a tool, update it — though deny
rules written with the old names keep working with vmware-policy ≥ 1.18 (`vmware-audit policy` lists any rule the
renames widened), and vmware-pilot ≥ 1.13 translates old names in saved workflows.
If an install older than 2.0 shares that `rules.yaml`, give it vmware-policy ≥ 1.18.2 too: it matches each rule
against both the old and the new name, so one file serves both.

## Capabilities (17 MCP tools — 16 read / 1 write)

| Category | Tools | Read/Write |
|----------|-------|:----------:|
| **GPU inventory** | host list / detail, device list, vGPU consumer list | 4 read |
| **GPU utilization** | live utilization per vGPU VM (GPU %, memory %, temperature) | 1 read |
| **GPU readiness** | per-host vGPU/PAIS readiness verdict + blocking reasons | 1 read |
| **Profile catalog** | vGPU profile list, DirectPath profile list | 2 read |
| **Profile validation** | read-only pre-check before changing a vGPU profile (power state + whether the host offers it) | 1 read |
| **vGPU assignment** | set a VM's vGPU profile (the VM must be powered off) | 1 write |
| **Private AI Service** | served models, model catalog, knowledge bases, data sources | 4 read |
| **PAIS monitoring** | cluster GPU summary (utilization/memory/temperature, hot/idle, busiest) | 1 read |
| **Sizing & air-gap** | LLM GPU/storage sizing advisor, local pais.yml mirror manifest | 2 read |

Reads are strictly non-destructive. The one write (`assign_vgpu`) previews its blast radius, refuses a powered-on
VM, **never powers a VM off itself**, double-confirms on the CLI, and is audited to `~/.vmware/audit.db`;
pre-check with `validate_vgpu_profile` first.
`assign_vgpu` takes `confirm` (default `false`). Without it the call returns `blast_radius` — the VM's name and id,
current and target profile, whether its vGPU device is edited or a new one added, power state, `blockers` and
`unmeasured` — and changes nothing. `confirm=true` is refused for a powered-on or suspended VM, a name shared by
several VMs, and a VM whose power state or devices cannot be read. It never powers a VM off itself.
`get_pais_model_catalog` / `list_pais_data_sources` use unconfirmed PAIS control-plane paths (pitfall #36: a 404
returns a base-URL teaching hint and is not a bug); `advise_pais_sizing` / `verify_pais_bundle` need no connection
(pure computation / local file parsing).

## Quick Start

```bash
uv tool install vmware-privateai
vmware-privateai version
vmware-privateai gpu host-list        # first read command — lists the hosts that have GPUs
```

Configuration lives in `~/.vmware-privateai/config.yaml` (targets + an optional `pais:` section); passwords and the
PAIS bearer token live in `~/.vmware-privateai/.env` (chmod 600). See
`skills/vmware-privateai/references/setup-guide.md`.

## Common Workflows

**1. Find an idle GPU and change a VM's vGPU profile**
```bash
vmware-privateai gpu device-list --vendor NVIDIA   # vm_count 0 = idle
vmware-privateai gpu consumer-list                 # who holds a vGPU, with which profile
vmware-privateai vgpu profile-list --host esx-07   # profiles this host can assign
vmware-privateai gpu vgpu-assign fin-train-01 grid_a100-4c --dry-run   # preview
# power the VM off with vmware-aiops first, then:
vmware-privateai gpu vgpu-assign fin-train-01 grid_a100-4c            # double-confirm + audit
```
*Failure branch*: on "VM is powered on", power it off first with vmware-aiops `power_off_vm` (CLI:
`vmware-aiops vm power-off fin-train-01`); if the host does not offer the profile or memory is short, check the
valid profiles with `vmware-privateai gpu host-get <that host>`.

**2. Fleet-wide GPU utilization sweep**
```bash
vmware-privateai gpu utilization --top 10
```
*Failure branch*: `metrics unavailable` is not an error — the NVIDIA host GPU driver does not expose that VM's
counters. Deeper per-SM / MIG-slice telemetry is not provided by vSphere (it needs NVIDIA DCGM).

**3. See what PAIS is serving**
```bash
vmware-privateai pais model-list
vmware-privateai pais kb-list
```
*Failure branch*: HTTP 404 is usually a base-URL mismatch (the `/api/v1` prefix varies by deployment and is
unconfirmed in beta) — check `pais.endpoint`; HTTP 401/403 means `VMWARE_PRIVATEAI_PAIS_TOKEN` expired or lacks
permission — fetch a new token.

## Companion Skills
- [vmware-aiops](https://github.com/vmware-skills/VMware-AIops) — the vCenter VMs behind AI workloads (power/snapshot/clone/migrate)
- [vmware-vks](https://github.com/vmware-skills/VMware-VKS) — GPU-enabled Tanzu Kubernetes
- [vmware-monitor](https://github.com/vmware-skills/VMware-Monitor) — read-only vSphere health

This skill is the GPU lens over all three.

## Security
See `SECURITY.md` at the repo root. Highlights: credentials only in `.env` (chmod 600, `b64:` obfuscation); TLS
verification on by default; all vSphere/PAIS text passes `vmware_policy.sanitize()` against prompt injection;
read/write authorization is left to vCenter RBAC; `assign_vgpu` is power-off gated + double-confirmed + audited.

## License
MIT
