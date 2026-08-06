# Security Policy

## Disclaimer
Community-maintained open-source project, **not affiliated with, endorsed by, or sponsored by VMware,
Inc., Broadcom Inc., or NVIDIA Corporation.** "VMware", "vSphere", and "VCF" are trademarks of Broadcom;
"NVIDIA" and "vGPU" are trademarks of NVIDIA Corporation.

## Reporting Vulnerabilities
Please open a GitHub private security advisory at
https://github.com/vmware-skills/VMware-PrivateAI/security/advisories or email the maintainer. Do not
file public issues for security reports.

## Security Design

### Credential Management
- vCenter/ESXi passwords and the PAIS bearer token live only in `~/.vmware-privateai/.env` (chmod 600),
  obfuscated to `b64:` at rest (obfuscation, not encryption; it defeats casual grep / shoulder-surfing,
  not a determined reader). Config files hold host / username / port and the `pais.endpoint` only.
- Per-target env vars `VMWARE_PRIVATEAI_<TARGET>_PASSWORD` (and the `VMWARE_PRIVATEAI_PAIS_TOKEN` bearer
  token) can be injected from a secret manager (Vault / CyberArk / AWS SM / Kubernetes Secret) instead
  of `.env` — the code reads the environment either way.
- Username + password are resolved together on every call (both are lazily-read properties) so a
  rotating credential sidecar never half-updates the pair.

### Authorization — delegated to vCenter RBAC
The skill ships full read + one write and does not gate read-vs-write itself. Point a target at a
read-only vCenter service account and the single write (`vgpu_assign`'s `ReconfigVM_Task`) is refused at
vCenter, un-bypassably — the one place the control cannot be stepped around by a shell. Recommend a
dedicated service account scoped to the GPU clusters.

### `vgpu_assign` write safety
`vgpu_assign` is the only state-changing tool. Its safety model:
- **Power-off gate.** A vGPU change requires the VM to be powered **off** (no hot-add, no vMotion for
  passthrough — VERIFIED). `confirm=false` (the default) previews only — current profile, target
  profile, power state, and `requires_power_off` — without acting. `confirm=true` applies it but refuses
  a powered-on VM with a teaching error.
- **Never powers the VM off itself.** Powering the VM off is `vmware-aiops`'s job, kept separate so this
  tool's blast radius stays "one VM, when it is already off". The refusal routes the operator to
  `vmware-aiops vm_power_off`.
- **Edits only the existing vGPU device.** The reconfigure targets the VM's existing vGPU device
  (selected by the same `backing.vgpu` predicate the preview uses), never a plain DirectPath / SR-IOV
  `VirtualPCIPassthrough`, so it cannot silently convert a full-GPU passthrough into a vGPU device.
- **Honest outcome.** It waits for the real `ReconfigVM_Task` to finish and reports/audits the actual
  result (a bad profile or insufficient framebuffer fails asynchronously) — never a premature "ok".
- **CLI double-confirm + `--dry-run`.** The CLI `gpu vgpu-assign` command requires two confirmations and
  supports `--dry-run`.
- **Audited.** Every applied change (and every error) is recorded to `~/.vmware/audit.db` via the
  `@vmware_tool` harness, and to the CLI companion `~/.vmware-privateai/audit.log`.

### PAIS response sanitization
Private AI Service REST responses are the highest-value prompt-injection surface in this skill — a
knowledge-base description is operator/attacker-authored free text. All PAIS-supplied strings (model ids,
`owned_by`, KB id/name/status/description), and all vSphere-supplied strings (device / vendor / VM /
vGPU-profile names), pass through `vmware_policy.sanitize()` (≤500-char truncation + C0/C1 control-char
stripping) at projection time before they reach the model.

### SSL/TLS Verification
On by default. `verify_ssl: false` (per vCenter/ESXi target) and `pais.verify_ssl: false` are intended
only for self-signed lab certificates. The PAIS client centralizes all HTTP / transport error
translation in one place (`_request`) so a bad token or 404 surfaces a teaching message, not a raw
traceback (踩坑 #37).

### Transitive Dependencies
Runtime deps: pyvmomi, httpx, typer, rich, pyyaml, python-dotenv, mcp, and `vmware-policy` (the family's
shared audit/policy/sanitize harness). No urllib3, no requests.

## Static Analysis
```bash
uvx bandit -r vmware_privateai/     # target: 0 Medium+ issues
```

## Supported Versions
| Version | Supported |
|---------|-----------|
| 1.0.x   | ✅ (beta) |
