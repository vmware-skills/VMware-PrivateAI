<!-- mcp-name: io.github.vmware-skills/vmware-privateai -->

# VMware Private AI (Foundation with NVIDIA) — GPU & Model-Serving Ops

> **Disclaimer**: This is a community-maintained open-source project and is **not affiliated with,
> endorsed by, or sponsored by VMware, Inc., Broadcom Inc., or NVIDIA Corporation.** "VMware", "vSphere",
> and "VCF" are trademarks of Broadcom; "NVIDIA" is a trademark of NVIDIA. Source is auditable at
> [github.com/vmware-skills/VMware-PrivateAI](https://github.com/vmware-skills/VMware-PrivateAI) under the MIT license.

> **Status: pre-MVP skeleton (2026-08-06).** Skill #15 of the VMware family. Manages the **GPU / AI-infrastructure
> layer** of VMware Private AI Foundation with NVIDIA (PAIF-N) on vSphere 9.x / VCF 9.1 — GPU host/device
> inventory, vGPU & DirectPath profiles, GPU consumers and utilization, vGPU assignment, and Private AI
> Service (PAIS) model serving. Independent 1.x version line.

Companion of **vmware-aiops** (the vCenter VMs behind AI workloads), **vmware-vks** (GPU-enabled Kubernetes),
and **vmware-monitor** (read-only health). This skill is the GPU lens over all three.

Every API path is verified against official Broadcom/NVIDIA docs before use (see
`tests/eval/spec/privateai_endpoints.py`) — no endpoints written from memory.

The one write, `vgpu_assign`, takes `confirm` (default `false`). Without it the call returns
`blast_radius` — the VM's name and id, current and target profile, whether its vGPU device is edited or a
new one added, power state, `blockers` and `unmeasured` — and changes nothing. `confirm=true` is refused
for a powered-on or suspended VM, a name shared by several VMs, and a VM whose power state or devices
cannot be read. It never powers a VM off itself.
