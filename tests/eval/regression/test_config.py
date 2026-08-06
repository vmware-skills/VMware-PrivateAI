"""Regression tests for config loading & secret handling.

Covers the widened secret-key obfuscation (review M3: *_TOKEN, not just *_PASSWORD), the
teaching error on a malformed target entry (review LOW: no raw KeyError), and the accurate
setup guidance in operator-facing errors (review H5: no reference to non-existent
'vmware-privateai init' / 'doctor' commands or a 'config.example.yaml' that ships nowhere).
"""

from __future__ import annotations

import base64

import pytest

from vmware_privateai import config as cfg
from vmware_privateai.config import ConfigError

# The whole CLI surface — messages must not tell the operator to run anything outside this set.
_REAL_COMMANDS = {"version", "mcp", "gpu", "vgpu", "pais"}
_PHANTOM_STRINGS = ("vmware-privateai init", "vmware-privateai doctor", "config.example.yaml")


# --- M3: *_TOKEN secrets get grep-safe obfuscation, not just *_PASSWORD ---------

def test_secret_key_re_matches_password_and_token():
    assert cfg._SECRET_KEY_RE.fullmatch("VMWARE_PRIVATEAI_VC_PASSWORD")
    assert cfg._SECRET_KEY_RE.fullmatch("VMWARE_PRIVATEAI_PAIS_TOKEN")  # would stay plaintext before M3
    assert not cfg._SECRET_KEY_RE.fullmatch("VMWARE_PRIVATEAI_VC_USERNAME")


def test_autoencode_obfuscates_a_bearer_token(tmp_path):
    env = tmp_path / ".env"
    env.write_text("VMWARE_PRIVATEAI_PAIS_TOKEN=super-secret-bearer\n", encoding="utf-8")
    cfg._autoencode_env_file(env)
    contents = env.read_text(encoding="utf-8")
    assert "super-secret-bearer" not in contents  # no grep-able plaintext token
    assert "b64:" in contents
    # round-trips back to the exact original value on read
    encoded = contents.split("=", 1)[1].strip()
    assert base64.b64decode(encoded[4:]).decode() == "super-secret-bearer"


# --- LOW: malformed target entry teaches instead of raising a raw KeyError -------

def test_malformed_target_missing_host_raises_teaching_config_error():
    with pytest.raises(ConfigError) as ei:
        cfg._build_target({"name": "vc-prod"})  # no 'host'
    msg = str(ei.value)
    assert "Malformed target" in msg and "host" in msg and "vc-prod" in msg


def test_malformed_target_not_a_dict_raises_teaching_config_error():
    with pytest.raises(ConfigError):
        cfg._build_target("just-a-string")  # type: ignore[arg-type]


def test_well_formed_target_still_builds():
    t = cfg._build_target({"name": "vc-prod", "host": "vcenter.example.com"})
    assert t.name == "vc-prod" and t.host == "vcenter.example.com"


# --- H5: operator-facing errors reference only real commands / files ------------

def _password_error_message() -> str:
    t = cfg.TargetConfig(name="vc-prod", host="h", config_username="u")
    try:
        _ = t.password  # unset -> ConfigError
    except ConfigError as exc:
        return str(exc)
    raise AssertionError("expected a ConfigError for an unset password")


def test_password_error_names_real_setup_steps_not_phantom_commands(monkeypatch):
    monkeypatch.delenv("VMWARE_PRIVATEAI_VC_PROD_PASSWORD", raising=False)
    msg = _password_error_message()
    for phantom in _PHANTOM_STRINGS:
        assert phantom not in msg, f"error message references non-existent {phantom!r} (踩坑 #43)"
    assert "VMWARE_PRIVATEAI_VC_PROD_PASSWORD" in msg and "chmod 600" in msg


def test_no_configured_targets_error_avoids_phantom_commands():
    with pytest.raises(ValueError) as ei:
        _ = cfg.AppConfig().default_target
    msg = str(ei.value)
    for phantom in _PHANTOM_STRINGS:
        assert phantom not in msg


def test_missing_config_file_error_avoids_phantom_commands(tmp_path):
    with pytest.raises(FileNotFoundError) as ei:
        cfg.load_config(tmp_path / "nope.yaml")
    msg = str(ei.value)
    for phantom in _PHANTOM_STRINGS:
        assert phantom not in msg
    assert "targets:" in msg  # points at the real manual step


def test_pais_missing_config_file_error_avoids_phantom_commands(tmp_path):
    from vmware_privateai.pais import config as pais_cfg

    with pytest.raises(FileNotFoundError) as ei:
        pais_cfg.load_pais_config(tmp_path / "nope.yaml")
    msg = str(ei.value)
    for phantom in _PHANTOM_STRINGS:
        assert phantom not in msg
    assert "pais:" in msg  # points at the real manual step
