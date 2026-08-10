"""CLI: vGPU / DirectPath profile catalog (read) — profile-list, directpath-list."""

from __future__ import annotations

from typing import Annotated

import typer

from vmware_privateai.cli._common import (
    ConfigOption,
    TargetOption,
    _get_connection,
    cli_errors,
    console,
)

vgpu_app = typer.Typer(help="vGPU / DirectPath profile catalog: what profiles a host/vCenter offers.")


@vgpu_app.command("profile-list")
@cli_errors
def vgpu_profile_list_cmd(
    host: Annotated[str, typer.Option("--host", help="Filter/scope by host name (substring)")] = "",
    model: Annotated[str, typer.Option("--model", help="Filter by profile/model name (substring)")] = "",
    target: TargetOption = None,
    config: ConfigOption = None,
) -> None:
    """List the available vGPU profile catalog, aggregated across hosts."""
    from vmware_privateai.ops.vgpu_profiles import list_vgpu_profiles

    out = list_vgpu_profiles(_get_connection(target, config), host=host or None, model=model or None)
    console.print(f"\n[bold cyan]vGPU profiles ({out['returned']}/{out['total']}):[/]")
    for p in out["items"]:
        fb = f"{p['framebuffer_gib']}GiB" if p["framebuffer_gib"] is not None else "-"
        console.print(
            f"  [cyan]{p['profile']}[/]  fb={fb}  class={p['profile_class'] or '-'}  "
            f"sharing={p['sharing'] or '-'}  hosts={p['host_count']}"
        )
    if out["truncated"]:
        console.print(f"  [dim]{out['hint']}[/]")


@vgpu_app.command("directpath-list")
@cli_errors
def directpath_list_cmd(
    name: Annotated[str, typer.Option("--name", help="Filter by profile name (substring)")] = "",
    vendor: Annotated[str, typer.Option("--vendor", help="Filter by vendor (substring)")] = "",
    target: TargetOption = None,
    config: ConfigOption = None,
) -> None:
    """List vCenter-level DirectPath profiles (vSphere 9.0+)."""
    from vmware_privateai.ops.vgpu_profiles import list_directpath_profiles

    out = list_directpath_profiles(_get_connection(target, config), name=name or None, vendor=vendor or None)
    console.print(f"\n[bold cyan]DirectPath profiles ({out['returned']}/{out['total']}):[/]")
    for p in out["items"]:
        console.print(f"  [cyan]{p['name']}[/]  id={p['id']}  vendor={p['vendor'] or '-'}  {p['description']}")
    if out["truncated"]:
        console.print(f"  [dim]{out['hint']}[/]")


@vgpu_app.command("validate")
@cli_errors
def vgpu_profile_validate_cmd(
    vm_name: Annotated[str, typer.Argument(help="VM whose vGPU profile would change")],
    target_profile: Annotated[str, typer.Argument(help="Target vGPU profile (e.g. nvidia_a100-8c)")],
    target: TargetOption = None,
    config: ConfigOption = None,
) -> None:
    """Pre-flight a vGPU profile change (read-only): would setting VM to target_profile succeed?"""
    from vmware_privateai.ops.validate import validate_vgpu_change

    out = validate_vgpu_change(_get_connection(target, config), vm_name, target_profile)
    verdict = "[green]can apply[/]" if out["can_apply"] else "[yellow]blocked[/]"
    console.print(
        f"\n[bold cyan]{out['vm']}[/] on [cyan]{out['host'] or '?'}[/]  {verdict}  "
        f"({out['current_profile'] or 'none'} → {out['target_profile']}, {out['power_state'] or '?'})"
    )
    for reason in out["blocking_reasons"]:
        console.print(f"  [yellow]- {reason}[/]")
    console.print(f"  [dim]{out['hint']}[/]")
