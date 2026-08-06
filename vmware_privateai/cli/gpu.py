"""CLI: GPU inventory (read) — host-list, host-get, device-list, consumer-list."""

from __future__ import annotations

from typing import Annotated

import typer

from vmware_privateai.cli._common import (
    ConfigOption,
    DryRunOption,
    TargetOption,
    _audit,
    _double_confirm,
    _get_connection,
    _resolve_target,
    cli_errors,
    console,
    guarded,
)

gpu_app = typer.Typer(help="GPU inventory: hosts, physical devices, vGPU consumers.")


@gpu_app.command("host-list")
@cli_errors
def gpu_host_list_cmd(
    name: Annotated[str, typer.Option("--name", help="Filter by host name (substring)")] = "",
    vendor: Annotated[str, typer.Option("--vendor", help="Filter by GPU vendor (substring)")] = "",
    target: TargetOption = None,
    config: ConfigOption = None,
) -> None:
    """List hosts that have a GPU."""
    from vmware_privateai.ops.gpu import list_gpu_hosts

    out = list_gpu_hosts(_get_connection(target, config), name=name or None, vendor=vendor or None)
    console.print(f"\n[bold cyan]GPU hosts ({out['returned']}/{out['total']}):[/]")
    for h in out["items"]:
        console.print(
            f"  [cyan]{h['host']}[/]  gpus={h['gpu_count']}  "
            f"vendors={','.join(h['vendors']) or '-'}  vgpu_vms={h['vgpu_vms']}"
        )
    if out["truncated"]:
        console.print(f"  [dim]{out['hint']}[/]")


@gpu_app.command("host-get")
@cli_errors
def gpu_host_get_cmd(
    host_name: Annotated[str, typer.Argument(help="Host name (from host-list)")],
    target: TargetOption = None,
    config: ConfigOption = None,
) -> None:
    """Full GPU detail for one host."""
    from vmware_privateai.ops.gpu import get_gpu_host

    out = get_gpu_host(_get_connection(target, config), host_name)
    console.print(f"\n[bold cyan]{out['host']}[/] — {len(out['gpus'])} GPU(s):")
    for g in out["gpus"]:
        console.print(
            f"  {g['device']}  [{g['type']}]  {g['vendor']}  "
            f"{g['memory_mb']} MB  pci={g['pci_id']}  vms={g['vm_count']}"
        )


@gpu_app.command("device-list")
@cli_errors
def gpu_device_list_cmd(
    host: Annotated[str, typer.Option("--host", help="Filter by host name (substring)")] = "",
    vendor: Annotated[str, typer.Option("--vendor", help="Filter by GPU vendor (substring)")] = "",
    target: TargetOption = None,
    config: ConfigOption = None,
) -> None:
    """List physical GPU devices across hosts."""
    from vmware_privateai.ops.gpu import list_gpu_devices

    out = list_gpu_devices(_get_connection(target, config), host=host or None, vendor=vendor or None)
    console.print(f"\n[bold cyan]GPU devices ({out['returned']}/{out['total']}):[/]")
    for d in out["items"]:
        console.print(
            f"  {d['host']}  {d['device']}  [{d['type']}]  "
            f"{d['vendor']}  {d['memory_mb']} MB  vms={d['vm_count']}"
        )
    if out["truncated"]:
        console.print(f"  [dim]{out['hint']}[/]")


@gpu_app.command("utilization")
@cli_errors
def gpu_utilization_cmd(
    vm: Annotated[str, typer.Option("--vm", help="Filter by VM name (substring)")] = "",
    top: Annotated[int, typer.Option("--top", help="Keep only the N busiest VMs")] = 0,
    target: TargetOption = None,
    config: ConfigOption = None,
) -> None:
    """Real-time GPU utilization per vGPU VM (busiest first)."""
    from vmware_privateai.ops.utilization import gpu_utilization

    out = gpu_utilization(_get_connection(target, config), vm=vm or None, top=top or None)
    console.print(f"\n[bold cyan]GPU utilization ({out['returned']}/{out['total']}):[/]")
    for i in out["items"]:
        if i["metrics_available"]:
            console.print(
                f"  [cyan]{i['vm']}[/]  {i['profile']}  gpu={i['gpu_pct']}%  "
                f"mem={i['mem_pct']}%  temp={i['temp_c']}C{'  [dim](idle)[/]' if i['idle'] else ''}"
            )
        else:
            console.print(f"  [cyan]{i['vm']}[/]  {i['profile']}  [dim]metrics unavailable (no host driver?)[/]")
    if out["truncated"]:
        console.print(f"  [dim]{out['hint']}[/]")


@gpu_app.command("vgpu-assign")
@cli_errors
@guarded(risk_level="high")
def vgpu_assign_cmd(
    vm_name: Annotated[str, typer.Argument(help="VM to reconfigure (from consumer-list)")],
    profile: Annotated[str, typer.Argument(help="Target vGPU profile (from host-get)")],
    target: TargetOption = None,
    config: ConfigOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Set a VM's vGPU profile (VM must be powered off). Double-confirm + --dry-run."""
    from vmware_privateai.ops.assign import assign_vgpu

    si = _get_connection(target, config)
    tname = _resolve_target(target)
    preview = assign_vgpu(si, vm_name, profile, confirm=False)
    console.print(
        f"  vm=[cyan]{preview['vm']}[/]  {preview['current_profile'] or '(none)'} -> {preview['target_profile']}  "
        f"power={preview['power_state']}  requires_power_off={preview['requires_power_off']}"
    )
    if dry_run:
        console.print("[magenta][DRY-RUN] no change applied.[/]")
        return
    _double_confirm(f"set vGPU to {profile}", vm_name, tname)
    out = assign_vgpu(si, vm_name, profile, confirm=True, audit_logger=_audit, target_name=tname)
    console.print(f"[green]{out['hint']}[/]")


@gpu_app.command("consumer-list")
@cli_errors
def gpu_consumer_list_cmd(
    profile: Annotated[str, typer.Option("--profile", help="Filter by vGPU profile (substring)")] = "",
    vm: Annotated[str, typer.Option("--vm", help="Filter by VM name (substring)")] = "",
    target: TargetOption = None,
    config: ConfigOption = None,
) -> None:
    """List VMs consuming a vGPU and the profile each holds."""
    from vmware_privateai.ops.gpu import list_gpu_consumers

    out = list_gpu_consumers(_get_connection(target, config), profile=profile or None, vm=vm or None)
    console.print(f"\n[bold cyan]vGPU consumers ({out['returned']}/{out['total']}):[/]")
    for c in out["items"]:
        console.print(f"  [cyan]{c['vm']}[/]  {c['profile']}")
    if out["truncated"]:
        console.print(f"  [dim]{out['hint']}[/]")
