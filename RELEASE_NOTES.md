## v1.1.1 — a dropped connection no longer keeps itself alive

Every `connect()` registered an `atexit` cleanup that closes over the
ServiceInstance, and `atexit` held that closure — and so the SI, its stub and
its socket — until the process exited. Nothing ever unregistered it. A
long-running MCP server that reconnects after each session expiry therefore
pinned one dead connection per reconnect, and at exit would run a `Disconnect`
against every session it had ever opened.

Measured before the fix: 20 evict-and-reconnect cycles left all 20 evicted
ServiceInstance objects reachable. The `id(si)` side stores were correctly down
to one entry throughout — the side-store discipline was never the leak, the
registration was.

`_release_si()` now takes the handler back off at both points that drop a
connection: the eviction inside `connect()` and `disconnect()`. Five repos had
the identical shape, so `family_smoke` gained a gate for it (154 → 155).

Not `WeakKeyDictionary`, which looks like the obvious fix and is a regression:
pyVmomi's `ManagedObject.__eq__` compares moId, class and serverGuid, and every
ServiceInstance carries moId `'ServiceInstance'` with serverGuid `None`. Two
vCenters collapse into one entry — connecting to the second silently hands the
first one's `verify_ssl` to both. Keying by `id()` is right precisely because
it is identity.

## v1.1.0 — a doctor, so a failure has somewhere to look

This was the only skill in the family with no self-check of any kind. An operator
whose config or credentials were wrong had nowhere to start: the first symptom
was a failing tool call, with no way to tell a bad password from an unreachable
vCenter from a missing pyVmomi. `vmware-privateai doctor` runs the checks in the
order a failure actually cascades — config file, `.env` and its permissions, SDK,
then the connection itself. Verified against a live vCenter 8.0.3.

Also: `.env` permissions go through `vmware_policy.fsperms` instead of POSIX mode
bits, which are absent on Windows and made the old `chmod 600` remedy inert on
the platform it was warning about.

## v1.0.4 — the test suite runs on a non-UTF-8 machine, and the guardrail tests with it


**The suite now runs on a cp936 machine.** Round 3 of the VCF 9 field testing ran
on Windows Server 2025 with locale cp936. Across the family four repos' suites --
1687 tests -- never executed at all, dying at collection reading our own UTF-8
sources, and 101 more failed the same way. Most of those were the tests that
verify the destructive-operation guardrails: the guardrails were fine, the tests
that check them could not open a file. On the UTF-8 CI every one of them was
green. A security test that cannot run is not a security test.

Every text read and write here names its encoding now, `tests/` included -- the
previous round fixed only the package, which is why this came back. A gate in
`family_smoke` scans both trees by AST, and the whole family's suites were re-run
under an ASCII locale to confirm: 15 of 15 green, from 1 of 15.

**`--help` no longer dies on a console that cannot encode it.** On any console
whose encoding cannot carry the characters in our own help text, `--help` exited
with a `UnicodeEncodeError` traceback -- unavailable exactly on the machines
where it is most needed. Four repos were affected; the handler is now relaxed in
all fifteen so a glyph degrades instead of killing the command.

**Its environment resolver no longer answers for other skills.**
`set_environment_resolver` wrote one process-global slot and twelve servers
registered into it at import time, so the last one won for all of them --
measured taking a `freeze-production-writes` rule from DENY to ALLOW on another
skill's production target. Registration is keyed by skill now (requires
vmware-policy 1.12.0).

**Unknown tool arguments are refused instead of dropped.** The schema declared
`additionalProperties: false` and the runtime accepted them anyway, so a filter
argument whose name a model guessed wrong returned the *unfiltered* result with
nothing to indicate anything had been discarded. Fixed in vmware-policy 1.12.0
and in force here.

Requires vmware-policy 1.12.0.

## v1.0.3 — the environment resolver this skill could always answer

The environment resolver is registered. `environment_for` has been in this
skill's config since it shipped and the wiring was never done, so every target
read as undeclared and no environment-scoped policy rule could match it. The
family gate that should have caught this iterated a hand-written list of repos
that did not include this one.

**The `vmware-policy` floor moves to >=1.11.0.** Policy 1.11.0 stops the engine
failing open: on a host whose locale is not UTF-8, reading `rules.yaml` raised a
decode error that was swallowed, and a `freeze-production-writes` rule came back
ALLOW. No new API is used here, so the floor could have stayed — it is raised
because leaving it low means a user resolving 1.10.0 keeps the permissive engine
and the fix never reaches them. One behaviour travels with it: on a host whose
rules file cannot be read, operations move from all-allowed to all-denied.
`VMWARE_POLICY_DISABLED=1` is checked above the rules, so the escape hatch does
not itself depend on them loading.

Also in this release: the suite no longer appends to the operator's real
`~/.vmware/audit.db`. It held over 30,000 rows dominated by tool names nobody
had invoked, including 1,400 entries for a destructive operation that never
happened — an audit trail carrying test fiction cannot answer the question it is
kept for.

## v1.0.2 — the schema an agent reads now carries the descriptions

Parameter descriptions reach the JSON schema for the first time. An MCP client
sees the schema, not the docstring, and this repo's coverage of `description`
and `additionalProperties` was 0% — while nearly every parameter was already
described in an `Args:` block no client ever receives.

Measured on a real VCF 9.1 estate, the gap produced a silent failure with no
error at any stage: a parameter name guessed wrong is discarded and the tool
returns the full unfiltered result; a value guessed wrong (`power_state=
"running"`) returns 0 rows where there were 11.

vmware-policy 1.10.0's `describe_tool_parameters` copies what is already
written, so the docstring is now load-bearing and the two cannot drift apart. It
removes the `Args:` block from the description once copied — both travel in
every `tools/list` response, so leaving it bills the same sentences twice
against the manifest's token budget. `additionalProperties` is closed: an open
schema is room for a model to invent arguments that are then silently
discarded, which is the other half of the same failure.

**The `vmware-policy` floor moves to >=1.10.0.** Older releases have no
`describe_tool_parameters`, and resolving one gives an ImportError at server
start rather than a missing feature.


# Release Notes — vmware-privateai

## v1.0.1 — reclaiming `latest` on ClawHub

**No functional change.** This release exists to fix a distribution defect, and
the version number is the fix.

The first release of this skill went out as 1.0.0 by mistake and was re-versioned
to 0.1.0 the same day, on the reasoning that a beta belongs on a 0.x line. PyPI
and the MCP Registry both resolve `latest` by upload order, so they followed the
0.x line without complaint. **ClawHub resolves it by version order.** 1.0.0
outranks every 0.x, so from the day it was published until today, `clawhub
install @zw008/vmware-privateai` handed the user the withdrawn first build — 10
tools, none of the seven read/pre-flight tools added in 0.2.0, and neither of the
two defects fixed in 0.2.1. Three subsequent releases were invisible on that
channel.

Going to 1.0.1 overtakes 1.0.0 in that ordering without deleting a published
version anyone may already depend on.

**This is not a maturity claim.** The skill is still beta in substance and the
caveats are unchanged: GET-response field names, the exact PAIS REST path, and
the `gpu.*` perf-counter entity type remain defensive and pending validation on
live VCF 9.1 hardware with an NVIDIA driver; MIG has no vSphere API. SKILL.md
now says exactly this where it used to say "v0.1.0 (beta)".

Contents are identical to v0.2.1: 17 tools (16 read, 1 write).

### Lesson recorded

Lowering a version number costs more than it looks. The family handbook already
warned that a downgrade fights the ordering on PyPI and the registry; it did not
know ClawHub picks the maximum. A withdrawn version is only withdrawn on the
channels that agree it is.

## v0.2.1 — two wrong numbers: the server's own version, and the advertised tool count

Both defects were invisible to the test suites and both were user-facing.

- **The MCP server reported the SDK's version as its own.** `FastMCP` accepts no
  `version` argument and leaves the lowlevel server's at `None`; with it `None`
  the SDK answers `initialize` with its OWN version. Every skill in the family
  therefore told its client it was mcp 1.29.1 — a number that exists for no
  package here, and one that would change with an SDK bump and no code change of
  ours. Verified end to end rather than by reading: unset the field and a probe
  server reports the installed SDK's version; set it and it reports ours.
- **server.json advertised a stale tool count.** That number is what MCP Registry
  publishes and what the plugin manifest and marketplace copy, so one stale
  integer was wrong in three public places. Corrected against the registered
  tools: 10 advertised, 17 real. README and SKILL.md were already right.

Also new: this repo is installable as a Claude Code plugin
(`/plugin install vmware-privateai@vmware-skills`). The skill and its MCP server arrive in
one step; nothing is duplicated, the manifest points at the existing `skills/`
tree. family_smoke gained three gates — the server's reported version, the plugin
manifest's agreement with pyproject, and the advertised tool count against the
live registration.

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
