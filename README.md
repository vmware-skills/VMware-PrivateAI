<!-- mcp-name: io.github.vmware-skills/vmware-privateai -->

# VMware-PrivateAI

Inspect GPU readiness and VMware Private AI resources before deployment.

English · [简体中文](README-CN.md)

Community-maintained · MIT · Not an official VMware/Broadcom/NVIDIA product.

[![18-second illustrative investigation demo](https://raw.githubusercontent.com/vmware-skills/marketplace/main/assets/demos/vmware-skills-en.gif)](https://github.com/vmware-skills/marketplace/blob/main/assets/demos/vmware-skills-en.mp4)

Synthetic-data animation, not a live recording. [Watch the AI-narrated family introduction (~2 min)](https://github.com/vmware-skills/marketplace/blob/main/assets/demos/vmware-skills-en.mp4)

## Why use it?

- Inspect GPU/vGPU inventory, profiles and utilization.
- Query supported PAIS catalogs and model resources.
- Use local sizing and air-gap bundle inspection.

## Get started

Install the published CLI and inspect its commands:

```sh
uv tool install vmware-privateai==2.5.3
vmware-privateai --help
```

Requires uv and Python >=3.11. Configure targets and credentials locally before remote queries. [Setup guide](skills/vmware-privateai/references/setup-guide.md).

For Claude Code and other host installation paths, see the [family portal](https://github.com/vmware-skills/marketplace).

## Know the boundaries

Includes GPU assignment and related sensitive VM operations. Unknown API shapes stay unknown; PAIS live-validation limits and required Python 3.11+ are documented.

## Go deeper

- [Capabilities](skills/vmware-privateai/references/capabilities.md)
- [CLI reference](skills/vmware-privateai/references/cli-reference.md)
- [Detailed usage and architecture](skills/vmware-privateai/references/detailed-guide.md)
- [Canonical skill instructions](skills/vmware-privateai/SKILL.md)

Part of [VMware Skills](https://github.com/vmware-skills/marketplace): 15 skills, 14 MCP servers and one shared Policy library. If it helps your work, star the family portal.

[Issues and feedback](https://github.com/vmware-skills/VMware-PrivateAI/issues) · [MIT](LICENSE)
