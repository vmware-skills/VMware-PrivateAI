# vmware-privateai — Setup Guide

> **Disclaimer**: Community-maintained open-source project, **not affiliated with, endorsed by, or
> sponsored by VMware, Inc., Broadcom Inc., or NVIDIA Corporation.** "VMware", "vSphere", and "VCF" are
> trademarks of Broadcom; "NVIDIA" and "vGPU" are trademarks of NVIDIA. Source is publicly auditable
> under the MIT license.

Install, credential, and MCP-client configuration for vmware-privateai, plus the Security section.

## 1. Install

```bash
uv tool install vmware-privateai       # isolated tool env; puts vmware-privateai on PATH
vmware-privateai version
```

Requires Python 3.11+ (the MCP server is reflected by FastMCP/Pydantic; older interpreters can raise on
PEP 604 unions — 踩坑 #33). Runtime deps: pyvmomi, httpx, typer, rich, pyyaml, python-dotenv, mcp, and
`vmware-policy` (the family audit/policy harness, installed automatically).

## 2. Configure targets

Create `~/.vmware-privateai/config.yaml`:

```yaml
targets:
  - name: vc-prod
    host: vcenter-prod.example.com
    username: administrator@vsphere.local   # optional; env var overrides this
    type: vcenter                            # or esxi
    port: 443
    verify_ssl: true
    environment: production                  # optional label for policy scoping

# Optional — only needed for the pais model-list / kb-list tools:
pais:
  endpoint: https://pais.example.com         # base URL; the /api/v1 prefix is added by the client
  verify_ssl: true
```

The first target is the default (used when `--target` / the `target` MCP arg is omitted).

## 3. Credentials — never in config files

Passwords and the PAIS bearer token live only in `~/.vmware-privateai/.env`:

```bash
mkdir -p ~/.vmware-privateai
cat >> ~/.vmware-privateai/.env <<'EOF'
VMWARE_PRIVATEAI_VC_PROD_PASSWORD=your-vcenter-password
VMWARE_PRIVATEAI_PAIS_TOKEN=your-oidc-bearer-token
EOF
chmod 600 ~/.vmware-privateai/.env
```

- **Per-target password**: `VMWARE_PRIVATEAI_<TARGET>_PASSWORD`, where `<TARGET>` is the target `name`
  upper-cased with `-` replaced by `_` (so target `vc-prod` → `VMWARE_PRIVATEAI_VC_PROD_PASSWORD`).
- **Optional username override**: `VMWARE_PRIVATEAI_<TARGET>_USERNAME` wins over `config.yaml` (resolved
  together with the password on every call, so a rotating sidecar never splits the pair).
- **PAIS token**: `VMWARE_PRIVATEAI_PAIS_TOKEN` — a short-lived OIDC/OAuth2 bearer token from your
  Identity Provider (the PAIS API uses `Authorization: Bearer <token>`). It is a secret and is
  obfuscated to `b64:` at rest exactly like a password.
- **Secret manager**: any of these vars can be injected from Vault / CyberArk / AWS Secrets Manager /
  a Kubernetes Secret instead of `.env` — the code reads the environment either way.

On load, plaintext `*_PASSWORD` / `*_TOKEN` values in `.env` are auto-rewritten to grep-safe `b64:`
form (obfuscation, not encryption — it defeats casual grep / shoulder-surfing, not a determined reader).

## 4. MCP client configuration

Preferred (installed console script — no `uvx` network re-resolve, works through enterprise TLS
proxies, 踩坑 #25):

```json
{
  "mcpServers": {
    "vmware-privateai": {
      "command": "vmware-privateai",
      "args": ["mcp"],
      "env": { "VMWARE_PRIVATEAI_CONFIG": "~/.vmware-privateai/config.yaml" }
    }
  }
}
```

Fallback (`uvx` — re-resolves from PyPI each start; if your network runs a TLS MitM proxy, add
`"UV_NATIVE_TLS": "true"` to `env`):

```json
{
  "mcpServers": {
    "vmware-privateai": {
      "command": "uvx",
      "args": ["--from", "vmware-privateai", "vmware-privateai-mcp"],
      "env": { "VMWARE_PRIVATEAI_CONFIG": "~/.vmware-privateai/config.yaml" }
    }
  }
}
```

This layout works with any MCP-compatible client (Claude Desktop, Claude Code, Goose, etc.).

## 5. Verify

```bash
vmware-privateai gpu host-list             # lists hosts that have a GPU
vmware-privateai vgpu profile-list         # the assignable vGPU profile catalog
vmware-privateai pais model-list           # PAIS served models (if configured)
```

A teaching error here names exactly what to fix (missing password, unresolvable host, TLS, missing PAIS
endpoint/token) — follow the message.

## Security

> **Disclaimer**: not affiliated with, endorsed by, or sponsored by VMware, Inc., Broadcom Inc., or
> NVIDIA Corporation. See the full policy in the repo-root `SECURITY.md`.

1. **Source Code** — https://github.com/vmware-skills/VMware-PrivateAI (MIT), publicly auditable.
2. **Config File Contents** — `config.yaml` holds only target host/username/port and the
   `pais.endpoint`. No passwords, no tokens. Secrets live in `~/.vmware-privateai/.env` (chmod 600,
   `b64:` obfuscated at rest).
3. **Webhook Data Scope** — none. No webhooks and no outbound network calls except to the configured
   vCenter/ESXi targets and the PAIS endpoint.
4. **TLS Verification** — on by default. `verify_ssl: false` (per target) and `pais.verify_ssl: false`
   are intended only for self-signed lab certificates.
5. **Prompt Injection Protection** — all vSphere-supplied and PAIS-supplied text (device / vendor / VM
   / vGPU-profile names, PAIS model ids, and knowledge-base descriptions — the highest-value injection
   surface here) passes through `vmware_policy.sanitize()` (≤500-char truncation + C0/C1 control-char
   stripping) before it reaches the model.
6. **Least Privilege** — read-vs-write authorization is delegated to the vCenter service account's RBAC
   role: a read-only account refuses `vgpu_assign`'s ReconfigVM at vCenter, un-bypassably (the one place
   the control cannot be stepped around by a shell). Recommend a dedicated service account scoped to the
   GPU clusters. All writes are recorded to `~/.vmware/audit.db` (and the CLI companion
   `~/.vmware-privateai/audit.log`).

### Static analysis

```bash
uvx bandit -r vmware_privateai/            # target: 0 Medium+ issues
```
