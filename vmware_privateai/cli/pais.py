"""CLI: Private AI Service (PAIS) reads — model-list, kb-list.

These talk to the PAIS REST endpoint (config.yaml 'pais:' section) with the bearer token in
VMWARE_PRIVATEAI_PAIS_TOKEN — separate from the vCenter connection.
"""

from __future__ import annotations

from typing import Annotated

import typer

from vmware_privateai.cli._common import ConfigOption, cli_errors, console

pais_app = typer.Typer(help="Private AI Service: served models and knowledge bases (REST).")


def _client(config: str | None):
    from pathlib import Path

    from vmware_privateai.pais.client import PaisClient
    from vmware_privateai.pais.config import load_pais_config

    cfg = load_pais_config(Path(config)) if config else load_pais_config()
    return PaisClient(cfg)


@pais_app.command("model-list")
@cli_errors
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
