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
import sys


def _harden_console_encoding() -> None:
    """Never let one unrepresentable glyph kill a command.

    On a console whose encoding cannot carry the characters we print -- cp936 on
    the Chinese Windows boxes this family is tested on, or any ASCII locale --
    ``print`` raises ``UnicodeEncodeError`` and the whole command dies with a
    traceback. ``--help`` died that way in four repos. A mangled dash is a
    cosmetic loss; a dead ``--help`` is an outage, so the error handler is
    relaxed rather than the vocabulary narrowed.

    Best effort: ``reconfigure`` is absent when stdout has been replaced by a
    plain object (pytest capture, some MCP hosts), and losing the hardening
    there is not worth an exception at import.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError, OSError):
            pass


_harden_console_encoding()

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
def doctor() -> None:
    """Diagnose config, credentials, SDK and vCenter connectivity."""
    from vmware_privateai.doctor import run_doctor

    raise typer.Exit(0 if run_doctor() else 1)


@app.command()
def mcp() -> None:
    """Run the MCP server over stdio (used by MCP clients as `vmware-privateai mcp`)."""
    from vmware_privateai.mcp_server.server import main

    main()
