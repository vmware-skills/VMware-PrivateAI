"""PAIS bundle / manifest inspector (LOCAL file parse — NO network, NO registry pull).

Air-gapped PAIS delivery repeatedly stalls on "what images does this bundle actually pull, and
from which registries do I have to mirror?" (SR 36978884 offline pull; pais.yml image-URL issues).
This op parses a LOCAL pais.yml / manifest with a real YAML parser (never a regex over the file —
踩坑 #38: read-modify structured files with their own parser), enumerates every container image
reference, and flags the two things that break an air-gap: images with NO immutable tag/digest,
and images from PUBLIC registries that must be mirrored inside the enclave.

It does NOT contact any registry or the PAIS API — it only reads the file you point it at, so it
works before anything is deployed. Registry reachability is a deliberate non-goal (that needs
network egress an air-gapped operator does not have).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from vmware_privateai.ops._errors import PrivateAiError
from vmware_privateai.ops._sanitize import _sanitize

# Public registries that an air-gapped site must mirror internally.
_PUBLIC_REGISTRIES = frozenset(
    {
        "docker.io",
        "registry-1.docker.io",
        "ghcr.io",
        "quay.io",
        "nvcr.io",
        "gcr.io",
        "registry.k8s.io",
        "public.ecr.aws",
    }
)
# A YAML file larger than this is not a hand-authored bundle manifest — refuse rather than slurp.
_MAX_BYTES = 5 * 1024 * 1024
# Keys whose value carries a container image reference — singular ("image", "gpuImage") or plural
# ("images"), as a string, a Helm-style {repository,tag} block, or a list of either.
_IMAGE_KEY = re.compile(r"(^|_|-|\.)images?$", re.IGNORECASE)
# A plausible container reference: has a '/' or a ':tag' and no spaces.
_REF_SHAPE = re.compile(r"^[\w][\w./:@-]+$")


def _looks_like_ref(candidate: str) -> bool:
    candidate = candidate.strip()
    if not candidate or " " in candidate or not _REF_SHAPE.match(candidate):
        return False
    return "/" in candidate or ":" in candidate


def _assemble_ref(block: dict) -> str | None:
    """Build a ref string from a Helm-style structured image block, or None if it has no repo.

    Handles ``{repository: "nvcr.io/x", tag: "1"}`` and ``{registry: "nvcr.io", repository: "x",
    tag: "1", digest: "sha256:.."}`` — the repository may or may not already carry the registry.
    """
    repo = block.get("repository") or block.get("image") or block.get("name")
    if not isinstance(repo, str) or not repo.strip():
        return None
    repo = repo.strip()
    registry = block.get("registry")
    if isinstance(registry, str) and registry.strip() and not repo.startswith(registry.strip()):
        repo = f"{registry.strip()}/{repo}"
    tag = block.get("tag")
    digest = block.get("digest")
    if tag not in (None, "") and "@" not in repo and ":" not in repo.rsplit("/", 1)[-1]:
        repo = f"{repo}:{tag}"
    if isinstance(digest, str) and digest.strip() and "@" not in repo:
        repo = f"{repo}@{digest.strip()}"
    return repo if _looks_like_ref(repo) else None


def _parse_ref(ref: str) -> dict:
    """Split a container image reference into registry / repository / tag / digest.

    Docker's implicit rule: the first slash-segment is the registry ONLY if it looks like a host
    (contains '.' or ':' or is 'localhost'); otherwise the registry is the implicit docker.io.
    """
    digest = ""
    remainder = ref
    if "@" in remainder:
        remainder, digest = remainder.split("@", 1)
    tag = ""
    # A ':' after the last '/' is the tag; a ':' inside the first segment is a registry port.
    last_slash = remainder.rfind("/")
    tail = remainder[last_slash + 1 :]
    if ":" in tail:
        name_tail, tag = tail.rsplit(":", 1)
        remainder = (remainder[: last_slash + 1] + name_tail) if last_slash >= 0 else name_tail
    first = remainder.split("/", 1)[0]
    if last_slash >= 0 and ("." in first or ":" in first or first == "localhost"):
        registry = first
        repository = remainder.split("/", 1)[1]
    else:
        registry = "docker.io"
        repository = remainder
    return {
        "ref": _sanitize(ref),
        "registry": _sanitize(registry),
        "repository": _sanitize(repository),
        "tag": _sanitize(tag),
        "digest": _sanitize(digest),
        "immutable": bool(digest) or (bool(tag) and tag != "latest"),
    }


def _collect_image_value(value: Any, out: list[str]) -> None:
    """Extract refs from the value under an image-ish key: str, structured block, or list of either."""
    if isinstance(value, str):
        if _looks_like_ref(value):
            out.append(value.strip())
    elif isinstance(value, dict):
        ref = _assemble_ref(value)
        if ref:
            out.append(ref)
        else:
            _walk_images(value, out)  # a nested container, not an image block — keep descending
    elif isinstance(value, list):
        for item in value:
            _collect_image_value(item, out)


def _walk_images(node: Any, out: list[str]) -> None:
    """Recursively collect container refs under any ``*image`` / ``*images`` key.

    Handles the three shapes a real manifest uses: a flat ``image: "ref"`` string, a Helm-style
    ``image: {repository, tag, ...}`` block, and a plural ``images: [...]`` list of either — so a
    structured or list manifest never silently yields zero images (踩坑 形态 #1, review H1).
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and _IMAGE_KEY.search(key):
                _collect_image_value(value, out)
            else:
                _walk_images(value, out)
    elif isinstance(node, list):
        for item in node:
            _walk_images(item, out)


def inspect_bundle(manifest_path: str) -> dict:
    """Enumerate container images in a local PAIS/manifest YAML and flag air-gap blockers.

    Returns the distinct image list (registry/repository/tag/digest), the set of registries to
    mirror, and warnings for mutable tags and public registries. Never contacts a registry —
    a wrong/missing path returns a teaching error, not a traceback.
    """
    path = Path(manifest_path).expanduser()
    if not path.exists():
        raise PrivateAiError(
            f"Manifest not found: '{manifest_path}'. Point pais_bundle_verify at a local pais.yml / "
            f"bundle manifest file (the one downloaded from the Broadcom portal)."
        )
    if not path.is_file():
        raise PrivateAiError(f"'{manifest_path}' is a directory, not a manifest file. Pass the pais.yml file itself.")
    if path.stat().st_size > _MAX_BYTES:
        raise PrivateAiError(
            f"'{manifest_path}' is larger than {_MAX_BYTES // (1024 * 1024)} MB — that is not a hand-authored "
            f"bundle manifest. Point this at the pais.yml, not a data/image archive."
        )
    try:
        text = path.read_text(encoding="utf-8")
        docs = list(yaml.safe_load_all(text))
    except (OSError, yaml.YAMLError) as exc:
        raise PrivateAiError(
            f"Could not parse '{manifest_path}' as YAML: {type(exc).__name__}. Confirm it is the "
            f"text pais.yml manifest (not a binary bundle)."
        ) from exc

    refs: list[str] = []
    for doc in docs:
        _walk_images(doc, refs)

    # De-duplicate, preserve first-seen order.
    seen: set[str] = set()
    images = []
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            images.append(_parse_ref(ref))
    images.sort(key=lambda i: (i["registry"], i["repository"], i["tag"]))

    registries = sorted({i["registry"] for i in images})
    public = sorted({r for r in registries if r in _PUBLIC_REGISTRIES})
    mutable = [i["ref"] for i in images if not i["immutable"]]

    warnings = []
    # Empty is NOT a clean pass — a false "0 images to mirror" would let an air-gap deploy fail later.
    # Surface it as a loud warning, not just a soft hint (review H1 / 踩坑 形态 #1).
    if not images:
        warnings.append(
            "NO container image references found — do NOT read this as 'nothing to mirror'. The PAIS "
            "manifest shape is unconfirmed (beta); verify this is the right pais.yml and check images "
            "manually before assuming an empty air-gap mirror list."
        )
    if public:
        warnings.append(f"{len(public)} public registry(ies) must be mirrored inside the air-gap: {', '.join(public)}.")
    if mutable:
        warnings.append(
            f"{len(mutable)} image(s) use a mutable tag (no digest, or ':latest') — pin a digest for "
            f"reproducible air-gapped pulls."
        )

    return {
        "manifest": str(path),
        "image_count": len(images),
        "images": images,
        "registries_to_mirror": registries,
        "public_registries": public,
        "mutable_images": mutable,
        "warnings": warnings,
        "hint": (
            "Mirror registries_to_mirror into your internal Harbor, then repoint pais.yml at it. "
            "This is a LOCAL parse — it does not verify the images are actually pullable."
            if images
            else "No images parsed — see warnings. Point pais_bundle_verify at the real pais.yml."
        ),
    }
