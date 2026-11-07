"""CLI: Private AI Service (PAIS) reads — models, knowledge, MCP gallery.

These talk to the PAIS REST endpoint (config.yaml 'pais:' section) with the bearer token in
VMWARE_PRIVATEAI_PAIS_TOKEN — separate from the vCenter connection.
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.markup import escape
from vmware_policy import audited, cli_local

from vmware_privateai.cli._common import ConfigOption, TargetOption, _get_connection, cli_errors, console

pais_app = typer.Typer(help="Private AI Service: models, knowledge bases, MCP gallery, monitoring, sizing, air-gap.")


def _client(config: str | None):
    from pathlib import Path

    from vmware_privateai.pais.client import PaisClient
    from vmware_privateai.pais.config import load_pais_config

    cfg = load_pais_config(Path(config)) if config else load_pais_config()
    return PaisClient(cfg)


@pais_app.command("model-list")
@cli_errors
@audited("list_pais_models")
def pais_model_list_cmd(
    name: Annotated[str, typer.Option("--name", help="Filter by model id (substring)")] = "",
    config: ConfigOption = None,
) -> None:
    """List models served by PAIS."""
    from vmware_privateai.ops.pais import list_models

    with _client(config) as client:
        out = list_models(client, name=name or None)
    console.print(f"\n[bold cyan]PAIS models ({out['returned']}/{out['total']}):[/]")
    for m in out["items"]:
        console.print(f"  [cyan]{m['id']}[/]  owned_by={m['owned_by'] or '-'}")
    if out["truncated"]:
        console.print(f"  [dim]{out['hint']}[/]")


@pais_app.command("kb-list")
@cli_errors
@audited("list_pais_knowledge_bases")
def pais_kb_list_cmd(
    name: Annotated[str, typer.Option("--name", help="Filter by KB name/id (substring)")] = "",
    config: ConfigOption = None,
) -> None:
    """List PAIS knowledge bases."""
    from vmware_privateai.ops.pais import list_knowledge_bases

    with _client(config) as client:
        out = list_knowledge_bases(client, name=name or None)
    console.print(f"\n[bold cyan]PAIS knowledge bases ({out['returned']}/{out['total']}):[/]")
    for kb in out["items"]:
        console.print(f"  [cyan]{kb['name'] or kb['id']}[/]  status={kb['status'] or '-'}  {kb['description']}")
    if out["truncated"]:
        console.print(f"  [dim]{out['hint']}[/]")


@pais_app.command("model-catalog")
@cli_errors
@audited("get_pais_model_catalog")
def pais_model_catalog_cmd(
    name: Annotated[str, typer.Option("--name", help="Filter by model id/name (substring)")] = "",
    config: ConfigOption = None,
) -> None:
    """List the PAIS model catalog — models available/approved to deploy (INFERRED path)."""
    from vmware_privateai.ops.pais import list_model_catalog

    with _client(config) as client:
        out = list_model_catalog(client, name=name or None)
    console.print(f"\n[bold cyan]PAIS model catalog ({out['returned']}/{out['total']}):[/]")
    for m in out["items"]:
        console.print(f"  [cyan]{m['id']}[/]  status={m['status'] or '-'}  source={m['source'] or '-'}")
    if out["truncated"]:
        console.print(f"  [dim]{out['hint']}[/]")


@pais_app.command("data-source-list")
@cli_errors
@audited("list_pais_data_sources")
def pais_data_source_list_cmd(
    name: Annotated[str, typer.Option("--name", help="Filter by data-source name/id (substring)")] = "",
    config: ConfigOption = None,
) -> None:
    """List PAIS RAG data sources (ingest connectors feeding knowledge bases; INFERRED path)."""
    from vmware_privateai.ops.pais import list_data_sources

    with _client(config) as client:
        out = list_data_sources(client, name=name or None)
    console.print(f"\n[bold cyan]PAIS data sources ({out['returned']}/{out['total']}):[/]")
    for d in out["items"]:
        console.print(f"  [cyan]{d['name'] or d['id']}[/]  type={d['type'] or '-'}  status={d['status'] or '-'}")
    if out["truncated"]:
        console.print(f"  [dim]{out['hint']}[/]")


@pais_app.command("mcp-servers")
@cli_errors
@audited("list_pais_mcp_servers")
def pais_mcp_servers_cmd(
    limit: Annotated[int, typer.Option("--limit", help="PAIS page size, 1-100")] = 50,
    after: Annotated[str, typer.Option("--after", help="Cursor from the preceding page")] = "",
    config: ConfigOption = None,
) -> None:
    """List PAIS MCP registrations (read-only; omits URLs and headers)."""
    from vmware_privateai.ops.pais import list_pais_mcp_servers

    with _client(config) as client:
        out = list_pais_mcp_servers(client, limit=limit, after=after or None)
    console.print(f"\n[bold cyan]PAIS MCP servers ({out['returned']} on this page):[/]")
    for item in out["items"]:
        name = escape(item["name"] or item["id"])
        item_id = escape(item["id"])
        availability = escape(item["availability"] or "-")
        console.print(
            f"  [cyan]{name}[/]  id={item_id}  availability={availability}"
        )
    if out.get("hint"):
        console.print(f"  [dim]{out['hint']}[/]")
    if out.get("next_after"):
        console.print(f"  [dim]next --after {escape(out['next_after'])}[/]")


@pais_app.command("mcp-tools")
@cli_errors
@audited("list_pais_mcp_tools")
def pais_mcp_tools_cmd(
    server_id: Annotated[str, typer.Option("--server-id", help="PAIS MCP server ID")] = "",
    approved: Annotated[bool, typer.Option("--approved", help="Show approved tools only")] = False,
    limit: Annotated[int, typer.Option("--limit", help="PAIS page size, 1-100")] = 50,
    after: Annotated[str, typer.Option("--after", help="Cursor from the preceding page")] = "",
    config: ConfigOption = None,
) -> None:
    """List PAIS MCP tool names and approval state (read-only)."""
    from vmware_privateai.ops.pais import list_pais_mcp_tools

    with _client(config) as client:
        out = list_pais_mcp_tools(client, server_id=server_id or None,
                                  is_approved=True if approved else None,
                                  limit=limit, after=after or None)
    console.print(f"\n[bold cyan]PAIS MCP tools ({out['returned']} on this page):[/]")
    for item in out["items"]:
        name = escape(item["name"] or item["id"])
        server = escape(item["server_name"] or item["server_id"])
        console.print(
            f"  [cyan]{name}[/]  server={server}  approved={item['is_approved']}"
        )
    if out.get("hint"):
        console.print(f"  [dim]{out['hint']}[/]")
    if out.get("next_after"):
        console.print(f"  [dim]next --after {escape(out['next_after'])}[/]")


@pais_app.command("monitoring-summary")
@cli_errors
@audited("get_pais_monitoring_summary")
def pais_monitoring_summary_cmd(
    hot_pct: Annotated[float, typer.Option("--hot-pct", help="gpu_pct at/above which a VM is 'hot'")] = 90.0,
    top: Annotated[int, typer.Option("--top", help="How many busiest VMs to show")] = 5,
    target: TargetOption = None,
    config: ConfigOption = None,
) -> None:
    """Fleet GPU monitoring rollup (util/mem/temp, hot/idle, busiest) — needs a vCenter connection."""
    from vmware_privateai.ops.monitoring import pais_monitoring_summary

    out = pais_monitoring_summary(_get_connection(target, config), hot_pct=hot_pct, top=top)
    console.print(
        f"\n[bold cyan]PAIS GPU rollup[/]  vGPU VMs={out['vgpu_vms']} reporting={out['reporting_vms']} "
        f"hot={out['hot_vms']} idle={out['idle_vms']}"
    )
    console.print(
        f"  gpu% avg={out['gpu_pct_avg']} max={out['gpu_pct_max']}  "
        f"mem% avg={out['mem_pct_avg']} max={out['mem_pct_max']}  temp_max={out['temp_c_max']}"
    )
    for v in out["busiest"]:
        console.print(f"    [cyan]{v['vm']}[/]  gpu={v['gpu_pct']}%  mem={v['mem_pct']}%  temp={v['temp_c']}")
    if out["hint"]:
        console.print(f"  [yellow]{out['hint']}[/]")


@pais_app.command("sizing")
@cli_errors
@cli_local("computes a sizing estimate locally; contacts nothing")
def pais_sizing_cmd(
    model: Annotated[str, typer.Option("--model", help="Model name containing size, e.g. llama-70b")] = "",
    billions: Annotated[float, typer.Option("--billions", help="Params in billions (overrides --model)")] = 0.0,
    precision: Annotated[str, typer.Option("--precision", help="fp16|bf16|fp8|int8|int4")] = "fp16",
) -> None:
    """Estimate GPU memory / count / storage for serving an LLM (pure heuristic, no connection)."""
    from vmware_privateai.ops.sizing import advise_sizing

    out = advise_sizing(model=model or None, model_billions=billions or None, precision=precision)
    console.print(
        f"\n[bold cyan]Sizing {out['model_billions']}B @ {out['precision']}[/]  "
        f"weights={out['weights_gib']}GiB  serving≈{out['serving_vram_gib']}GiB"
    )
    for o in out["gpu_options"]:
        console.print(f"    {o['gpu']:<12} {o['vram_gib']}GiB  →  {o['gpus_needed']} gpu(s)")
    console.print(f"  disk≈{out['storage']['model_on_disk_gib']}GiB  [dim]{out['storage']['iops_note']}[/]")
    console.print(f"  [dim]{out['assumptions']['note']}[/]")


@pais_app.command("bundle-verify")
@cli_errors
@cli_local("parses a local PAIS bundle manifest; contacts nothing")
def pais_bundle_verify_cmd(
    manifest_path: Annotated[str, typer.Argument(help="Path to a local pais.yml / bundle manifest")],
) -> None:
    """Parse a local pais.yml and list images/registries to mirror for air-gap (no network)."""
    from vmware_privateai.ops.bundle import inspect_bundle

    out = inspect_bundle(manifest_path)
    console.print(f"\n[bold cyan]Bundle images ({out['image_count']}):[/]  {out['manifest']}")
    for i in out["images"]:
        flag = "" if i["immutable"] else "  [yellow](mutable)[/]"
        console.print(f"  [cyan]{i['registry']}/{i['repository']}[/]:{i['tag'] or '-'}{flag}")
    if out["registries_to_mirror"]:
        console.print(f"  [bold]mirror:[/] {', '.join(out['registries_to_mirror'])}")
    for w in out["warnings"]:
        console.print(f"  [yellow]⚠ {w}[/]")
