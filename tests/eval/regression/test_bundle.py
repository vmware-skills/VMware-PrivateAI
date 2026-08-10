"""Regression tests for the local pais.yml bundle inspector (no network).

Covers image-reference parsing (registry/repo/tag/digest, docker.io implicit host), air-gap
warnings (public registry + mutable tag), YAML parsing via the real parser (踩坑 #38), and
teaching errors for a missing / non-file / non-YAML path.
"""

from __future__ import annotations

import pytest

from vmware_privateai.ops._errors import PrivateAiError
from vmware_privateai.ops.bundle import _parse_ref, inspect_bundle

_MANIFEST = """
services:
  serving:
    image: nvcr.io/nvidia/tritonserver:24.08-py3
  proxy:
    image: harbor.internal.corp/pais/envoy:v1.2.3
  cache:
    image: docker.io/library/redis
  pinned:
    image: ghcr.io/vmware/pais@sha256:abc123
"""


def test_parse_ref_splits_registry_repo_tag():
    out = _parse_ref("nvcr.io/nvidia/tritonserver:24.08-py3")
    assert out["registry"] == "nvcr.io"
    assert out["repository"] == "nvidia/tritonserver"
    assert out["tag"] == "24.08-py3"
    assert out["immutable"] is True


def test_parse_ref_implicit_dockerhub_registry():
    out = _parse_ref("library/redis")
    assert out["registry"] == "docker.io"  # no host-looking first segment -> implicit docker.io


def test_parse_ref_digest_is_immutable_even_without_tag():
    out = _parse_ref("ghcr.io/vmware/pais@sha256:abc123")
    assert out["digest"] == "sha256:abc123" and out["immutable"] is True


def test_parse_ref_latest_tag_is_mutable():
    assert _parse_ref("docker.io/library/redis:latest")["immutable"] is False


def test_inspect_bundle_enumerates_and_flags(tmp_path):
    p = tmp_path / "pais.yml"
    p.write_text(_MANIFEST, encoding="utf-8")
    out = inspect_bundle(str(p))
    assert out["image_count"] == 4
    assert "docker.io" in out["public_registries"] and "nvcr.io" in out["public_registries"]
    assert "harbor.internal.corp" not in out["public_registries"]  # internal registry is fine
    # docker.io/library/redis has no tag -> mutable warning
    assert any("mutable" in w for w in out["warnings"])
    assert any("public registry" in w for w in out["warnings"])


def test_inspect_bundle_missing_file_teaching_error():
    with pytest.raises(PrivateAiError) as ei:
        inspect_bundle("/no/such/pais.yml")
    assert "pais_bundle_verify" in str(ei.value) or "not found" in str(ei.value).lower()


def test_inspect_bundle_directory_teaching_error(tmp_path):
    with pytest.raises(PrivateAiError) as ei:
        inspect_bundle(str(tmp_path))
    assert "directory" in str(ei.value).lower()


def test_inspect_bundle_no_images_is_a_loud_warning_not_a_clean_pass(tmp_path):
    # review H1 / 踩坑 形态 #1: an empty result must NOT read as "nothing to mirror".
    p = tmp_path / "empty.yml"
    p.write_text("metadata:\n  name: nothing\n", encoding="utf-8")
    out = inspect_bundle(str(p))
    assert out["image_count"] == 0
    assert any("do NOT read this as" in w or "NO container image" in w for w in out["warnings"])


def test_inspect_bundle_extracts_helm_structured_image_block(tmp_path):
    # review H1: image: {repository, tag} (+ separate registry) must not silently yield [].
    p = tmp_path / "values.yml"
    p.write_text(
        "serving:\n  image:\n    registry: nvcr.io\n    repository: nvidia/tritonserver\n    tag: 24.08-py3\n",
        encoding="utf-8",
    )
    out = inspect_bundle(str(p))
    assert out["image_count"] == 1
    img = out["images"][0]
    assert img["registry"] == "nvcr.io" and img["repository"] == "nvidia/tritonserver" and img["tag"] == "24.08-py3"


def test_inspect_bundle_extracts_plural_images_list(tmp_path):
    # review H1: images: [ref, ref] (plural list of strings) must not silently yield [].
    p = tmp_path / "list.yml"
    p.write_text("images:\n  - nvcr.io/nvidia/a:1\n  - harbor.local/b:2\n", encoding="utf-8")
    out = inspect_bundle(str(p))
    assert out["image_count"] == 2
    assert {i["registry"] for i in out["images"]} == {"nvcr.io", "harbor.local"}


def test_inspect_bundle_non_yaml_teaching_error(tmp_path):
    p = tmp_path / "bad.yml"
    p.write_text("just: a: broken: yaml: :\n  - [unbalanced", encoding="utf-8")
    with pytest.raises(PrivateAiError):
        inspect_bundle(str(p))
