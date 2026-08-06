"""Shared CLI helpers for vmware-privateai.

Read commands wrap themselves in ``@cli_errors`` so a config / not-found / connection
problem prints its teaching message and exits 1, instead of dumping a traceback.
"""

from __future__ import annotations

import functools
import ssl
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from vmware_policy import guarded  # noqa: F401  (re-exported for write commands)

from vmware_privateai.config import ConfigError, load_config
from vmware_privateai.connection import ConnectionManager
from vmware_privateai.notify.audit import AuditLogger
from vmware_privateai.ops._errors import PrivateAiError

console = Console()
_audit = AuditLogger()

TargetOption = Annotated[str | None, typer.Option("--target", help="vCenter/ESXi target name from config.yaml")]
ConfigOption = Annotated[str | None, typer.Option("--config", help="Path to config.yaml (default ~/.vmware-privateai)")]
DryRunOption = Annotated[bool, typer.Option("--dry-run", help="Preview the change without applying it")]


def _resolve_target(target: str | None) -> str:
    return target or "default"


def _get_connection(target: str | None, config: str | None = None):
    """Build a ConnectionManager and connect to ``target`` — returns a pyVmomi ServiceInstance."""
    cfg = load_config(Path(config)) if config else None
    return ConnectionManager.from_config(cfg).connect(target)


def _double_confirm(action: str, resource: str, target: str = "default") -> None:
    """Require two confirmations for a destructive command; audit a 'rejected' entry on abort."""
    console.print(f"[bold yellow]⚠️  About to: {action} on '{resource}' (target '{target}')[/]")
    try:
        typer.confirm(f"Confirm 1/2: {action} on '{resource}'?", abort=True)
        typer.confirm(f"Confirm 2/2: really {action} on '{resource}'?", abort=True)
    except typer.Abort:
        _audit.record(target=target, operation=action, resource=resource, result="rejected")
        raise


def cli_errors(fn: Callable) -> Callable:
    """Print teaching errors cleanly (exit 1) instead of a traceback."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ssl.SSLError:
            # SSLError is a ValueError subclass — mask before the pass-through (review H2).
            console.print("[red]error:[/] TLS/certificate error (set verify_ssl: false for self-signed certs).")
            raise typer.Exit(1) from None
        except (ConfigError, PrivateAiError, FileNotFoundError, ConnectionError, ValueError) as exc:
            console.print(f"[red]error:[/] {exc}")
            raise typer.Exit(1) from None

    return wrapper
