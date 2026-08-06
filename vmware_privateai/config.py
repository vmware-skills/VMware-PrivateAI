"""Configuration for vmware-privateai.

Targets are vCenter/ESXi connections (GPU inventory, vGPU profiles, utilization
come from the vSphere API via pyVmomi). Passwords are NEVER stored in config
files — always via environment variables. Per-namespace Private AI Service
(PAIS) endpoints are configured separately (added with the model-serving vertical).
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from dotenv import dotenv_values, load_dotenv, set_key

CONFIG_DIR = Path.home() / ".vmware-privateai"
CONFIG_FILE = CONFIG_DIR / "config.yaml"
ENV_FILE = CONFIG_DIR / ".env"

_log = logging.getLogger("vmware-privateai.config")

# Secret-bearing env keys that get grep-safe b64: obfuscation. Both *_PASSWORD and
# *_TOKEN qualify — VMWARE_PRIVATEAI_PAIS_TOKEN is a bearer secret and must not sit in
# .env as grep-able plaintext just because it is not spelled "PASSWORD" (review M3).
_SECRET_KEY_RE = re.compile(r"[A-Z][A-Z0-9_]*_(PASSWORD|TOKEN)")


def _is_b64_token(value: str) -> tuple[bool, str]:
    """Return ``(True, decoded)`` if ``value`` is a valid ``b64:`` token, else ``(False, "")``.

    A value that merely *starts with* ``b64:`` but is not valid base64 (e.g. a
    real password ``b64:hunter2``) is NOT a token — treated as plaintext so it
    round-trips correctly instead of being corrupted.
    """
    if not value.startswith("b64:"):
        return (False, "")
    try:
        return (True, base64.b64decode(value[4:], validate=True).decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return (False, "")


def _decode_secret(value: str) -> str:
    """Decode a ``b64:`` token; any other value passes through. Obfuscation, not encryption."""
    ok, decoded = _is_b64_token(value)
    return decoded if ok else value


def _autoencode_env_file(env_file: Path) -> None:
    """Rewrite plaintext ``*_PASSWORD`` / ``*_TOKEN`` values in .env to grep-safe ``b64:`` form.

    Read/written through python-dotenv's own parser/serializer so the stored
    value is exactly what ``load_dotenv`` returns — the secret never drifts.
    Idempotent; only ``*_PASSWORD`` / ``*_TOKEN`` keys are touched. Obfuscation, not encryption.
    """
    if not env_file.exists():
        return
    try:
        parsed = dotenv_values(env_file)
    except OSError:
        return

    changed = False
    for key, value in parsed.items():
        if not value or not _SECRET_KEY_RE.fullmatch(key) or _is_b64_token(value)[0]:
            continue
        encoded = "b64:" + base64.b64encode(value.encode("utf-8")).decode("ascii")
        try:
            set_key(str(env_file), key, encoded, quote_mode="never")
            changed = True
        except OSError as exc:
            _log.warning("Could not auto-encode %s in %s: %s", key, env_file, exc)

    if not changed:
        return
    try:
        os.chmod(env_file, 0o600)
    except OSError:
        pass
    _log.warning(
        "Auto-encoded plaintext password(s) in %s to b64: (grep-safe; obfuscation, not encryption).",
        env_file,
    )


_autoencode_env_file(ENV_FILE)
load_dotenv(ENV_FILE)


def _check_env_permissions() -> None:
    """Warn if .env has permissions wider than owner-only (600)."""
    if not ENV_FILE.exists():
        return
    try:
        mode = ENV_FILE.stat().st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            _log.warning(
                "Security warning: %s has permissions %s (should be 600). Run: chmod 600 %s",
                ENV_FILE,
                oct(stat.S_IMODE(mode)),
                ENV_FILE,
            )
    except OSError:
        pass


_check_env_permissions()


class ConfigError(OSError):
    """A configuration problem the operator can fix, safe to show an agent.

    Subclasses ``OSError`` so CLI paths that catch ``OSError`` keep working; the
    narrow type lets the MCP ``_safe_error`` pass authored text through without
    leaking TLS/DNS/socket detail from a bare ``OSError``.
    """


@dataclass(frozen=True)
class TargetConfig:
    """A vCenter or ESXi connection target (GPU inventory source)."""

    name: str
    host: str
    config_username: str
    """Username as written in config.yaml. Read :attr:`username` instead — the
    env var overrides this, and the override is what actually gets used."""
    type: Literal["vcenter", "esxi"] = "vcenter"
    port: int = 443
    verify_ssl: bool = True
    environment: str = ""
    """Optional label (production / staging / lab). A policy ``deny`` rule may
    scope itself to an environment; a target that declares none is never matched."""

    @property
    def username(self) -> str:
        """Username for this target, env var winning over config.yaml.

        Resolved on every access, like :attr:`password` — reading it once at load
        time would split the credential pair a rotating secret sidecar keeps whole.
        """
        return os.environ.get(
            f"VMWARE_PRIVATEAI_{self.name.upper().replace('-', '_')}_USERNAME",
            self.config_username,
        )

    @property
    def password(self) -> str:
        env_key = f"VMWARE_PRIVATEAI_{self.name.upper().replace('-', '_')}_PASSWORD"
        pw = os.environ.get(env_key, "")
        if not pw:
            raise ConfigError(
                f"Password not found for target '{self.name}'. "
                f"Set environment variable {env_key}, or add the line "
                f"{env_key}=<password> to {ENV_FILE} and run 'chmod 600 {ENV_FILE}'."
            )
        return _decode_secret(pw)


@dataclass(frozen=True)
class AppConfig:
    """Top-level application config."""

    targets: tuple[TargetConfig, ...] = ()

    def get_target(self, name: str) -> TargetConfig:
        for t in self.targets:
            if t.name == name:
                return t
        available = ", ".join(t.name for t in self.targets)
        # ConfigError (not KeyError) so the teaching message passes _safe_error's allow-list
        # instead of being masked to a generic error (review LOW).
        raise ConfigError(
            f"Target '{name}' not found. Available: {available}. "
            f"Pass --target with one of those names, or add the target to {CONFIG_FILE} and re-run."
        )

    def environment_for(self, name: str | None) -> str:
        """Return the environment declared by ``name``, or by the default target."""
        try:
            target = self.get_target(name) if name else self.default_target
        except (ConfigError, KeyError, ValueError):
            return ""
        return target.environment

    @property
    def default_target(self) -> TargetConfig:
        if not self.targets:
            raise ValueError(
                f"No targets configured in {CONFIG_FILE}. Create it with a 'targets:' list "
                f"(each entry needs a 'name' and 'host'), then set "
                f"VMWARE_PRIVATEAI_<TARGET>_PASSWORD in {ENV_FILE} (chmod 600)."
            )
        return self.targets[0]


def load_config(config_path: Path | None = None) -> AppConfig:
    """Load config from YAML file, with env var overrides for passwords."""
    path = config_path or CONFIG_FILE
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            f"Create it with a 'targets:' list (each entry needs a 'name' and 'host'), then set "
            f"VMWARE_PRIVATEAI_<TARGET>_PASSWORD in {ENV_FILE} (chmod 600). Example:\n"
            f"  targets:\n"
            f"    - name: vc-prod\n"
            f"      host: vcenter.example.com"
        )

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    targets = tuple(_build_target(t) for t in raw.get("targets", []))

    return AppConfig(targets=targets)


def _build_target(t: dict) -> TargetConfig:
    """Build one TargetConfig, turning a missing ``name``/``host`` into a teaching error.

    A raw ``KeyError`` here would surface to the agent as an opaque ``'host'`` with no
    hint of which config entry is malformed (review LOW).
    """
    try:
        name, host = t["name"], t["host"]
    except (KeyError, TypeError) as exc:
        raise ConfigError(
            f"Malformed target entry in {CONFIG_FILE}: {t!r}. Each target needs both a "
            f"'name' and a 'host'. Fix the entry and re-run."
        ) from exc
    return TargetConfig(
        name=name,
        host=host,
        config_username=t.get("username", "administrator@vsphere.local"),
        type=t.get("type", "vcenter"),
        port=t.get("port", 443),
        verify_ssl=t.get("verify_ssl", True),
        environment=str(t.get("environment", "") or "").strip(),
    )
