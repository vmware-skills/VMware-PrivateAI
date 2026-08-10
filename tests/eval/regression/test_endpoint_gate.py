"""Strengthened anti-phantom-endpoint gate (踩坑 #36, review M1).

The per-vertical tests only assert a hand-declared ``SPEC_KEYS_USED ⊆ spec keys`` — they never
checked the ACTUAL pyVmomi ``pathSet`` strings the ops pass to ``_retrieve_props``, and the spec's
``ALLOWED_PATHS`` had zero test users. So a real property path (``runtime.powerState``) was used by
the code while absent from the verified index, invisibly.

This gate AST-scans the real call sites and asserts every pyVmomi property path / perf counter /
PAIS REST path the code actually touches is spec-verified, and wires ``ALLOWED_PATHS`` into the
assertion so it can no longer silently drift.
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib

import vmware_privateai.ops as ops_pkg
from vmware_privateai.ops import pais, utilization

OPS_DIR = pathlib.Path(ops_pkg.__file__).parent
# Every ops module that hands a literal pathSet to _retrieve_props.
_PYVMOMI_OPS_MODULES = (
    "gpu.py",
    "utilization.py",
    "assign.py",
    "vgpu_profiles.py",
    "readiness.py",
    "validate.py",
)


def _load_spec():
    path = pathlib.Path(__file__).parent.parent / "spec" / "privateai_endpoints.py"
    spec = importlib.util.spec_from_file_location("privateai_endpoints", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _retrieve_props_pathsets(module_path: pathlib.Path) -> set[str]:
    """Every string literal passed as the ``pathSet`` (3rd positional arg) to ``_retrieve_props``."""
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_retrieve_props"
            and len(node.args) >= 3
            and isinstance(node.args[2], ast.List)
        ):
            for elt in node.args[2].elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    found.add(elt.value)
    return found


def test_actual_pyvmomi_pathsets_are_all_spec_verified():
    # AST-scan the real _retrieve_props call sites — not a hand-declared list that can drift.
    spec = _load_spec()
    used: set[str] = set()
    for module in _PYVMOMI_OPS_MODULES:
        used |= _retrieve_props_pathsets(OPS_DIR / module)
    # Empty-result trap (踩坑 形态 #1): a scan that finds nothing must fail, not silently pass.
    assert used, "AST scan found zero _retrieve_props pathSets — the scan is broken, not the code"
    phantom = used - spec.VERIFIED_PROPERTY_PATHS
    assert not phantom, f"ops pass pyVmomi property paths absent from the verified spec (踩坑 #36): {phantom}"


def test_verified_property_paths_are_anchored_to_pyvmomi_objects():
    # Each allowed relative property path (except the universal `name`) must trace to a real
    # PYVMOMI_OBJECTS entry — so the property allow-list can't invent a path either.
    spec = _load_spec()
    assert spec.VERIFIED_PROPERTY_PATHS, "VERIFIED_PROPERTY_PATHS empty — a gate that checks nothing"
    for path in spec.VERIFIED_PROPERTY_PATHS - {"name"}:
        assert any(path in str(v["path"]) for v in spec.PYVMOMI_OBJECTS.values()), (
            f"{path!r} is not anchored to any PYVMOMI_OBJECTS entry"
        )


def test_allowed_paths_covers_the_pais_and_counter_paths_the_code_uses():
    # Wire ALLOWED_PATHS (previously zero test users): the PAIS REST paths and perf counters the
    # ops actually touch must all be inside it.
    spec = _load_spec()
    assert pais.PAIS_PATHS_USED <= spec.ALLOWED_PATHS, (
        f"ops/pais touches PAIS paths absent from ALLOWED_PATHS: {pais.PAIS_PATHS_USED - spec.ALLOWED_PATHS}"
    )
    assert utilization.SPEC_COUNTERS_USED <= spec.ALLOWED_PATHS, (
        f"utilization uses perf counters absent from ALLOWED_PATHS: "
        f"{utilization.SPEC_COUNTERS_USED - spec.ALLOWED_PATHS}"
    )
