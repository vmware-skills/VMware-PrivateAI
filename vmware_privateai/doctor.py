"""Environment diagnostics for VMware Private AI.

Added 2026-08-31: this was the only skill in the family with no self-check of
any kind, so an operator whose config or credentials were wrong had nowhere to
look — the first symptom was a failing tool call with no way to tell a bad
password from an unreachable vCenter from a missing pyVmomi.

Checks, in the order a failure actually cascades: config file, .env and its
permissions, the SDK, then the connection itself.
"""

from __future__ import annotations

import importlib
import logging

from rich.console import Console

from vmware_policy.fsperms import check_secret_file

from vmware_privateai.config import CONFIG_FILE, ENV_FILE, load_config

_log = logging.getLogger("vmware-privateai.doctor")
console = Console()


def _check(label: str, ok: bool, detail: str = "") -> bool:
    status = "[green]PASS[/green]" if ok else "[red]FAIL[/red]"
    line = f"  {status}  {label}"
    if detail:
        line += f"  [dim]({detail})[/dim]"
    console.print(line)
    return ok


def run_doctor() -> bool:
    """Run all diagnostic checks. Returns True if every check passed."""
    console.print("\n[bold]vmware-privateai doctor[/bold]\n")

    if not CONFIG_FILE.exists():
        console.print(
            f"[yellow]No config found.[/yellow] Create {CONFIG_FILE} and {ENV_FILE} "
            "(see config.example.yaml), then re-run.\n"
        )

    results: list[bool] = []
    results.append(
        _check("Config directory exists", CONFIG_FILE.parent.exists(), str(CONFIG_FILE.parent))
    )
    results.append(_check("config.yaml exists", CONFIG_FILE.exists(), str(CONFIG_FILE)))

    env_exists = ENV_FILE.exists()
    results.append(_check(".env file exists", env_exists, str(ENV_FILE)))
    if env_exists:
        # Three states, not two. Windows expresses no POSIX mode bits and
        # `chmod 600` exits 0 there without changing anything, so a plain
        # mode comparison would be permanently red on the platform it cannot
        # measure. See vmware_policy.fsperms.
        check = check_secret_file(ENV_FILE)
        results.append(
            _check(".env permissions restrict it to you", not check.is_failure, check.message)
        )

    try:
        pyvmomi = importlib.import_module("pyVmomi")
        results.append(_check("pyVmomi installed", True, getattr(pyvmomi, "__version__", "ok")))
    except ImportError:
        results.append(_check("pyVmomi installed", False, "pip install pyvmomi"))

    try:
        importlib.import_module("vmware_policy")
        results.append(_check("vmware-policy installed", True))
    except ImportError:
        results.append(_check("vmware-policy installed", False, "pip install vmware-policy"))

    if CONFIG_FILE.exists():
        try:
            cfg = load_config()
            targets = getattr(cfg, "targets", []) or []
            results.append(_check("At least one target configured", bool(targets), f"{len(targets)}"))
            for target in targets:
                name = getattr(target, "name", "?")
                try:
                    from vmware_privateai.connection import ConnectionManager

                    mgr = ConnectionManager(cfg)
                    si = mgr.connect(name)
                    about = si.content.about
                    results.append(
                        _check(f"vCenter '{name}' reachable", True, f"{about.version} build {about.build}")
                    )
                    mgr.disconnect(name)
                except Exception as exc:  # noqa: BLE001 — the failure is the answer
                    results.append(_check(f"vCenter '{name}' reachable", False, str(exc)[:80]))
        except Exception as exc:  # noqa: BLE001
            results.append(_check("Config loads", False, str(exc)[:80]))

    passed = sum(results)
    console.print(f"\n  {passed}/{len(results)} checks passed.\n")
    return all(results)
