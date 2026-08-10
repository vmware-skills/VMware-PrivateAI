"""Advisor MCP tools (2 read) — planning helpers that need NO vCenter / PAIS connection.

pais_sizing_advise (LLM GPU/storage sizing from a parameter count) and pais_bundle_verify (parse
a LOCAL pais.yml for air-gap image mirroring). Both are pure/local — no network — so they answer
design-time questions before anything is deployed. Signatures use ``Optional[X]`` (not PEP 604)
because FastMCP/Pydantic reflect them at import time (踩坑 #33).
"""

from __future__ import annotations

from typing import Optional

from vmware_policy import vmware_tool

from vmware_privateai.mcp_server._shared import _safe_error, mcp

_READ = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}


@mcp.tool(annotations=_READ)
@vmware_tool(risk_level="low")
def pais_sizing_advise(
    model: Optional[str] = None,
    model_billions: Optional[float] = None,
    precision: str = "fp16",
    kv_overhead: float = 1.3,
) -> dict:
    """[READ] Estimate GPU memory / GPU count / storage to serve an LLM of a given size.

    A transparent PLANNING heuristic (no connection): give a model name containing the size
    ("llama-70b") or model_billions (70). Returns weights_gib, serving_vram_gib, a per-GPU-model
    count table (L4/A100/L40S/H100/H200), model-on-disk GiB, and — importantly — that LLM
    inference is compute/HBM-bound so random IOPS is the wrong axis for the weights. Not
    authoritative; confirm against the model card and a real vLLM benchmark.

    Args:
        model: Model name containing the size, e.g. "llama-3-70b" or "27B".
        model_billions: Parameter count in billions (e.g. 70). Overrides `model`.
        precision: fp16 | bf16 | fp8 | int8 | int4 (default fp16).
        kv_overhead: Multiplier for KV cache + activations (default 1.3; raise to 1.5-2.0 for long context / high QPS).
    """
    try:
        from vmware_privateai.ops.sizing import advise_sizing

        return advise_sizing(model=model, model_billions=model_billions, precision=precision, kv_overhead=kv_overhead)
    except Exception as exc:  # noqa: BLE001
        return {"error": _safe_error(exc, "pais_sizing_advise")}


@mcp.tool(annotations=_READ)
@vmware_tool(risk_level="low")
def pais_bundle_verify(manifest_path: str) -> dict:
    """[READ] Parse a LOCAL pais.yml / bundle manifest and list the images to mirror for air-gap.

    Reads the file you point it at (no network, no registry pull), enumerates every container image
    reference, and flags the two things that break an air-gapped install: images on PUBLIC
    registries (must be mirrored inside the enclave) and images with a mutable tag / no digest.
    Returns images (registry/repository/tag/digest), registries_to_mirror, and warnings. A wrong
    path returns a teaching error. It does NOT verify the images are actually pullable.

    Args:
        manifest_path: Path to a local pais.yml / bundle manifest (from the Broadcom portal).
    """
    try:
        from vmware_privateai.ops.bundle import inspect_bundle

        return inspect_bundle(manifest_path)
    except Exception as exc:  # noqa: BLE001
        return {"error": _safe_error(exc, "pais_bundle_verify")}
