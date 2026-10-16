"""Regression: a bundle manifest path could point at this server's own files (HLD §18 step 0).

``verify_pais_bundle(manifest_path=…)`` reads a host file; over HTTPS any token
holder could have it read ``$OPS_HOME/mcp-tokens`` or a skill's ``.env``. It now
refuses a path that resolves into ``$OPS_HOME`` or a ``~/.vmware-*`` directory.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from vmware_policy.hostpaths import HostPathRefusedError

from vmware_privateai.ops.bundle import inspect_bundle


def test_a_manifest_inside_ops_home_is_not_read():
    home = Path(os.environ["OPS_HOME"])
    home.mkdir(parents=True, exist_ok=True)
    (home / "rules.yaml").write_text("rules: []\n", encoding="utf-8")
    with pytest.raises(HostPathRefusedError):
        inspect_bundle(str(home / "rules.yaml"))


def test_a_manifest_elsewhere_is_still_read(tmp_path):
    manifest = tmp_path / "bundle.yaml"
    manifest.write_text("images: []\n", encoding="utf-8")
    assert isinstance(inspect_bundle(str(manifest)), dict)
