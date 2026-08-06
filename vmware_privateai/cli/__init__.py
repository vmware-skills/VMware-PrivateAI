"""Typer CLI for vmware-privateai.

The ``mcp`` subcommand runs the stdio MCP server through the installed console
entry point (踩坑 #25: MCP clients should launch ``vmware-privateai mcp``, which
uses the PATH entry point and never re-resolves from PyPI like ``uvx`` does).
"""

from __future__ import annotations

import typer

from vmware_privateai import __version__
from vmware_privateai.cli.gpu import gpu_app
from vmware_privateai.cli.pais import pais_app
from vmware_privateai.cli.vgpu import vgpu_app

app = typer.Typer(
    help="VMware Private AI Foundation ops — GPU inventory, vGPU profiles, utilization, model serving.",
    no_args_is_help=True,
)

app.add_typer(gpu_app, name="gpu")
app.add_typer(vgpu_app, name="vgpu")
app.add_typer(pais_app, name="pais")


@app.command()
def version() -> None:
    """Print the vmware-privateai version."""
    typer.echo(__version__)


@app.command()
def mcp() -> None:
    """Run the MCP server over stdio (used by MCP clients as `vmware-privateai mcp`)."""
    from vmware_privateai.mcp_server.server import main

    main()
