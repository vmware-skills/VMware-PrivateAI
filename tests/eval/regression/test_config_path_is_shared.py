"""Policy judges a write from the same config file the write connects with.

Before 2026-09-11 the environment resolver in ``policy_environment.py`` honoured
``VMWARE_PRIVATEAI_CONFIG`` — ``mtime_cached_loader`` hands the variable's path to
the loader — while ``load_config()``, and so ``ConnectionManager.from_config()``,
the CLI and the doctor, always opened ``~/.vmware-privateai/config.yaml``. With
the variable set, the same target name could be labelled ``lab`` in the file
policy read and point at a production vCenter in the file the connection read:
a ``freeze-production-writes`` rule would allow the write. That is fail-open.

The fix is one precedence rule, ``config.resolve_config_path``, that every
reader goes through. These tests hold the two ends of the write together: the
environment vmware-policy resolves, and the host the connection dials.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from vmware_policy.environment import resolve_environment

import vmware_privateai.config as config_mod
import vmware_privateai.policy_environment  # noqa: F401  registers the resolver
from vmware_privateai.connection import ConnectionManager

_ENV = config_mod.CONFIG_ENV_VAR


def _write(path: Path, *, host: str, environment: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "targets:\n"
        f"  - name: vc-gpu\n"
        f"    host: {host}\n"
        f"    environment: {environment}\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def two_configs(tmp_path, monkeypatch):
    """The default file says lab; the file the variable names says production."""
    default = _write(tmp_path / "default" / "config.yaml", host="lab-vc.example", environment="lab")
    monkeypatch.setattr(config_mod, "CONFIG_FILE", default)
    chosen = _write(tmp_path / "chosen" / "config.yaml", host="prod-vc.example", environment="production")
    return default, chosen


def _dialled_host(monkeypatch) -> str:
    """The host a bare ConnectionManager.from_config() would connect to."""
    seen: list[str] = []

    def _fake_create(target):
        seen.append(target.host)
        return object()

    monkeypatch.setattr(ConnectionManager, "_create_connection", staticmethod(_fake_create))
    ConnectionManager.from_config().connect("vc-gpu")
    assert len(seen) == 1, seen
    return seen[0]


def test_with_the_variable_set_policy_and_connection_read_the_same_file(two_configs, monkeypatch):
    _, chosen = two_configs
    monkeypatch.setenv(_ENV, str(chosen))

    judged = resolve_environment("vc-gpu", skill="privateai")
    dialled = _dialled_host(monkeypatch)

    assert (judged, dialled) == ("production", "prod-vc.example"), (
        f"policy judged the write as {judged!r} while the connection dials {dialled!r} — "
        "the environment rule and the write read different config files"
    )


def test_every_reader_resolves_the_variable(two_configs, monkeypatch):
    """The bare calls the CLI, the doctor and the PAIS tools make."""
    from vmware_privateai.pais.config import load_pais_config

    _, chosen = two_configs
    chosen.write_text(chosen.read_text(encoding="utf-8") + "pais:\n  endpoint: https://pais.example\n",
                      encoding="utf-8")
    monkeypatch.setenv(_ENV, str(chosen))

    assert config_mod.resolve_config_path() == chosen
    assert config_mod.load_config().get_target("vc-gpu").host == "prod-vc.example"
    assert load_pais_config().endpoint == "https://pais.example"
    # The doctor asks the same function, so it never checks one file green while
    # the tools open another.
    from vmware_privateai import doctor

    assert getattr(doctor, "resolve_config_path", None) is config_mod.resolve_config_path


def test_an_explicit_path_still_wins_over_the_variable(two_configs, monkeypatch):
    default, chosen = two_configs
    monkeypatch.setenv(_ENV, str(chosen))
    assert config_mod.resolve_config_path(default) == default


def test_a_tilde_in_the_variable_is_expanded(tmp_path, monkeypatch):
    """The setup guide's MCP snippets set the variable to ``~/.vmware-privateai/...``.

    An MCP host passes that string through unexpanded; unexpanded, policy found
    no file and read every target as undeclared.
    """
    home = tmp_path / "home"
    _write(home / ".vmware-privateai" / "alt.yaml", host="prod-vc.example", environment="production")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv(_ENV, "~/.vmware-privateai/alt.yaml")

    assert config_mod.resolve_config_path() == home / ".vmware-privateai" / "alt.yaml"
    assert resolve_environment("vc-gpu", skill="privateai") == "production"
    assert _dialled_host(monkeypatch) == "prod-vc.example"
