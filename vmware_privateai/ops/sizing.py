"""PAIS / LLM serving sizing advisor (pure computation — NO API, NO connection).

Answers the single most-repeated field question ("how much GPU / storage / IOPS for a
27B / 70B / 123B model?") with a transparent, first-principles ESTIMATE, not an authoritative
spec. Every number here is a planning heuristic derived from the model's parameter count and
the requested precision — it carries its assumptions in the response so the operator can see
exactly how it was reached and adjust. There is no vSphere or PAIS call in this module.

Heuristics (stated, defensible, deliberately conservative):
  - Weights memory  ≈ params_billions × bytes_per_param   (fp16=2, int8=1, int4=0.5 bytes)
  - Serving vRAM    ≈ weights × kv_overhead + fixed_overhead_gib
    (kv_overhead accounts for the KV cache + activations + CUDA context; it GROWS with
     context length and concurrency, so this is a floor for light concurrency)
  - GPU count       = ceil(serving_vRAM / (per_gpu_vram_gib × usable_fraction))
  - Model on disk   ≈ weights × 1.15  (safetensors + config/tokenizer + a little slack)

Storage IOPS note (important, and honestly stated in the output): LLM *inference* is
compute/HBM-bandwidth bound, not random-IOPS bound. Disk speed governs model LOAD time
(sequential read throughput, MB/s — not random IOPS) and RAG vector-DB lookups (where random
IOPS matters). So this advisor reports a storage capacity + a load-throughput band, and is
explicit that "IOPS" is the wrong axis for the model weights themselves.
"""

from __future__ import annotations

import math
import re

# bytes per parameter by precision
_BYTES_PER_PARAM: dict[str, float] = {"fp16": 2.0, "bf16": 2.0, "fp8": 1.0, "int8": 1.0, "int4": 0.5}

# Common data-center GPU HBM sizes (GiB) we size a per-GPU count against.
_GPU_VRAM_GIB: dict[str, int] = {
    "L4": 24,
    "A100-40G": 40,
    "L40S": 48,
    "A100-80G": 80,
    "H100-80G": 80,
    "H200-141G": 141,
}

# Fraction of HBM actually usable for weights+KV after CUDA context / framework overhead.
_USABLE_FRACTION = 0.90
# Fixed per-deployment vRAM overhead (CUDA context, framework, small buffers), GiB.
_FIXED_OVERHEAD_GIB = 2.0


def _parse_billions(model: str | None, model_billions: float | None) -> float:
    """Resolve the parameter count in billions from an explicit number or a name like '70b'."""
    if model_billions is not None and model_billions > 0:
        return float(model_billions)
    if model:
        # Grab the first "<number>b" token (e.g. "llama-3.1-70b-instruct" -> 70, "27B" -> 27).
        match = re.search(r"(\d+(?:\.\d+)?)\s*[bB]\b", model)
        if match:
            return float(match.group(1))
    raise ValueError(
        "Provide the model size — either model_billions (e.g. 70) or a model name containing "
        "the size (e.g. 'llama-70b', '27B'). Sizing needs the parameter count to estimate GPU memory."
    )


def _load_band(disk_gib: float) -> str:
    """A qualitative model-LOAD expectation, framed as sequential throughput (not random IOPS)."""
    # ~1 GB/s sustained read is a reasonable all-flash floor; 3+ GB/s is NVMe/vSAN ESA territory.
    slow = disk_gib / 1.0  # seconds at 1 GB/s
    fast = disk_gib / 3.0  # seconds at 3 GB/s
    return (
        f"~{fast:.0f}-{slow:.0f}s to load once, governed by SUSTAINED READ THROUGHPUT "
        f"(≈1 GB/s all-flash to ≈3 GB/s NVMe/vSAN ESA), NOT random IOPS."
    )


def advise_sizing(
    *,
    model: str | None = None,
    model_billions: float | None = None,
    precision: str = "fp16",
    kv_overhead: float = 1.3,
) -> dict:
    """Estimate GPU memory / GPU count / storage for serving one model. Pure planning heuristic.

    Returns weights_gib, serving_vram_gib, a per-GPU-model count table, disk capacity, a
    load-throughput note, and the exact assumptions used. Not authoritative — a starting point
    for a design conversation, to be confirmed against the model card and a real benchmark.
    """
    precision = (precision or "fp16").lower()
    if precision not in _BYTES_PER_PARAM:
        raise ValueError(
            f"Unknown precision '{precision}'. Use one of: {', '.join(sorted(_BYTES_PER_PARAM))}. "
            f"fp16 is the safe default for a first estimate."
        )
    if kv_overhead < 1.0:
        raise ValueError("kv_overhead must be >= 1.0 (it multiplies the weights to add KV cache + activations).")

    billions = _parse_billions(model, model_billions)
    # Surface how the size was obtained — a number auto-extracted from a name is the biggest silent
    # failure mode (review M3): "Mixtral-8x7B" parses 7, not ~47B (MoE), and a versioned name can grab
    # the wrong token. The output must say so rather than presenting a name-derived guess as fact.
    size_note = ""
    if model_billions is None or model_billions <= 0:
        size_note = f"parsed {billions:g}B from name '{model}' — pass model_billions to override if wrong."
        if model and re.search(r"\d+\s*[xX]\s*\d+\s*[bB]", model):
            size_note += (
                " This looks like a Mixture-of-Experts 'NxM' name — the TOTAL parameter count usually "
                "differs from the per-expert size, so set model_billions to the real total."
            )
    bytes_per = _BYTES_PER_PARAM[precision]
    weights_gib = billions * bytes_per
    serving_vram = weights_gib * kv_overhead + _FIXED_OVERHEAD_GIB
    disk_gib = weights_gib * 1.15

    gpu_options = []
    for name, vram in sorted(_GPU_VRAM_GIB.items(), key=lambda kv: kv[1]):
        usable = vram * _USABLE_FRACTION
        count = max(1, math.ceil(serving_vram / usable))
        gpu_options.append({"gpu": name, "vram_gib": vram, "gpus_needed": count, "fits_single": serving_vram <= usable})

    return {
        "model_billions": billions,
        "size_note": size_note,
        "precision": precision,
        "weights_gib": round(weights_gib, 1),
        "serving_vram_gib": round(serving_vram, 1),
        "gpu_options": gpu_options,
        "storage": {
            "model_on_disk_gib": round(disk_gib, 1),
            "load_note": _load_band(disk_gib),
            "iops_note": (
                "LLM inference is compute/HBM-bandwidth bound — random IOPS is the WRONG axis for "
                "the model weights. Size storage for capacity + sequential load throughput. Random "
                "IOPS matters only for the RAG vector DB (pgvector/knowledge base), sized separately."
            ),
        },
        "assumptions": {
            "bytes_per_param": bytes_per,
            "kv_overhead_multiplier": kv_overhead,
            "fixed_overhead_gib": _FIXED_OVERHEAD_GIB,
            "usable_hbm_fraction": _USABLE_FRACTION,
            "note": (
                "PLANNING ESTIMATE for light concurrency. KV cache grows with context length and "
                "concurrent requests — raise kv_overhead (e.g. 1.5-2.0) for long-context / high-QPS. "
                "Confirm against the model card and a real vLLM benchmark before quoting a customer."
            ),
        },
    }
