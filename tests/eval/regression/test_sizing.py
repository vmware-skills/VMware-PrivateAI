"""Regression tests for the LLM sizing advisor (pure computation, no connection).

Covers the parameter-count parse (explicit vs. name-embedded), the precision-driven weights
formula, the GPU-count math, the honest IOPS framing, and the teaching errors on bad input.
"""

from __future__ import annotations

import math

import pytest

from vmware_privateai.ops.sizing import advise_sizing


def test_fp16_weights_are_two_bytes_per_param():
    out = advise_sizing(model_billions=70, precision="fp16")
    assert out["weights_gib"] == 140.0  # 70B * 2 bytes
    assert out["serving_vram_gib"] == round(140.0 * 1.3 + 2.0, 1)


def test_int4_quarters_the_weights_of_fp16():
    fp16 = advise_sizing(model_billions=8, precision="fp16")["weights_gib"]
    int4 = advise_sizing(model_billions=8, precision="int4")["weights_gib"]
    assert int4 == fp16 / 4


def test_size_parsed_from_model_name():
    assert advise_sizing(model="llama-3.1-70b-instruct")["model_billions"] == 70.0
    assert advise_sizing(model="Mixtral-27B")["model_billions"] == 27.0


def test_explicit_billions_overrides_name():
    assert advise_sizing(model="llama-70b", model_billions=13)["model_billions"] == 13.0


def test_gpu_count_is_ceil_of_serving_over_usable():
    out = advise_sizing(model_billions=70, precision="fp16")
    serving = out["serving_vram_gib"]
    h200 = next(o for o in out["gpu_options"] if o["gpu"] == "H200-141G")
    assert h200["gpus_needed"] == max(1, math.ceil(serving / (141 * 0.90)))


def test_small_model_fits_single_gpu_flag():
    out = advise_sizing(model_billions=7, precision="int4")
    a100 = next(o for o in out["gpu_options"] if o["gpu"] == "A100-80G")
    assert a100["gpus_needed"] == 1 and a100["fits_single"] is True


def test_iops_note_is_honest_about_the_wrong_axis():
    note = advise_sizing(model_billions=27)["storage"]["iops_note"].lower()
    assert "iops" in note and ("compute" in note or "bandwidth" in note)


def test_missing_size_raises_teaching_error():
    with pytest.raises(ValueError) as ei:
        advise_sizing()
    assert "model_billions" in str(ei.value)


def test_unknown_precision_raises_teaching_error():
    with pytest.raises(ValueError) as ei:
        advise_sizing(model_billions=7, precision="fp3")
    assert "precision" in str(ei.value).lower()


def test_kv_overhead_below_one_is_rejected():
    with pytest.raises(ValueError):
        advise_sizing(model_billions=7, kv_overhead=0.5)


def test_name_parse_echoes_size_note():
    # review M3: a number auto-extracted from a name must be surfaced, not presented as fact.
    out = advise_sizing(model="llama-70b")
    assert "parsed 70B from name" in out["size_note"]


def test_moe_name_warns_about_total_params():
    # review M3: "Mixtral-8x7B" parses 7, but total ~47B — the note must flag the MoE mismatch.
    out = advise_sizing(model="Mixtral-8x7B")
    assert out["model_billions"] == 7.0
    assert "Mixture-of-Experts" in out["size_note"] or "MoE" in out["size_note"]


def test_explicit_billions_has_no_parse_note():
    assert advise_sizing(model_billions=70)["size_note"] == ""
