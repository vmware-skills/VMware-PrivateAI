"""Private AI Service (PAIS) reads — served models and knowledge bases.

Paths (INFERRED_EXACT — see tests/eval/spec/privateai_endpoints.py PAIS_ENDPOINTS):
  - ``GET /api/v1/compatibility/openai/v1/models``  (OpenAI-style model list)
  - ``GET /api/v1/control/knowledge-bases``         (RAG knowledge bases)

The `/api/v1` prefix is corroborated by the rendered Broadcom developer portal but NOT by a
downloaded OpenAPI JSON or a live deployment, so it is DEFERRED (unconfirmed) — a 404 should
prompt a base-URL check, not be read as a bug (踩坑 #36). Responses are parsed defensively:
the exact JSON shape is unconfirmed, so both a bare list and the common ``{data|items|...: [...]}``
envelopes are accepted, and every field degrades via ``.get`` (踩坑 形态 #1).
"""

from __future__ import annotations

from typing import Any

from vmware_privateai.ops._paging import envelope
from vmware_privateai.ops._sanitize import _sanitize
from vmware_privateai.pais.client import PaisClient

# Exact REST path strings this module touches — asserted ⊆ spec PAIS paths by the gate (踩坑 #36).
_MODELS_PATH = "/api/v1/compatibility/openai/v1/models"
_KNOWLEDGE_BASES_PATH = "/api/v1/control/knowledge-bases"
PAIS_PATHS_USED: frozenset[str] = frozenset({_MODELS_PATH, _KNOWLEDGE_BASES_PATH})
# Spec keys — asserted ⊆ PAIS_ENDPOINTS by the gate.
PAIS_KEYS_USED: frozenset[str] = frozenset({"list_models", "list_knowledge_bases"})


def _as_rows(payload: Any, *keys: str) -> list:
    """Coerce an unconfirmed PAIS response into a list of row dicts.

    Accepts a bare JSON array, or a dict carrying the rows under any of ``keys``
    (e.g. "data", "items"). Anything else degrades to [] (checked-and-none, never a crash).
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _matches(text: str, needle: str | None) -> bool:
    return not needle or needle.lower() in (text or "").lower()


def _project_model(m: Any) -> dict:
    m = m if isinstance(m, dict) else {}
    # PAIS responses are the highest-value injection surface in this skill — sanitize every string.
    return {
        "id": _sanitize(m.get("id", "") or ""),
        "owned_by": _sanitize(m.get("owned_by", "") or ""),
        "created": m.get("created"),
    }


def list_models(client: PaisClient, *, name: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    """List models served by PAIS (OpenAI-compatible ``/models``), filtered by id substring. Paginated."""
    payload = client.get_json(_MODELS_PATH)
    models = [_project_model(m) for m in _as_rows(payload, "data", "models", "items")]
    models = [m for m in models if _matches(m["id"], name)]
    models.sort(key=lambda m: m["id"])
    return envelope(models, limit=limit, offset=offset)


def _project_knowledge_base(kb: Any) -> dict:
    kb = kb if isinstance(kb, dict) else {}
    # KB descriptions are operator/attacker-authored free text — the single highest-value
    # prompt-injection surface here. Sanitize id/name/status/description before the agent sees them.
    return {
        "id": _sanitize(kb.get("id", "") or kb.get("name", "") or ""),
        "name": _sanitize(kb.get("name", "") or ""),
        "status": _sanitize(kb.get("status", "") or ""),
        "description": _sanitize(kb.get("description", "") or ""),
    }


def list_knowledge_bases(
    client: PaisClient, *, name: str | None = None, limit: int = 50, offset: int = 0
) -> dict:
    """List PAIS knowledge bases (RAG vector stores), filtered by name substring. Paginated."""
    payload = client.get_json(_KNOWLEDGE_BASES_PATH)
    rows = _as_rows(payload, "knowledge_bases", "knowledgeBases", "items", "data")
    kbs = [_project_knowledge_base(kb) for kb in rows]
    kbs = [kb for kb in kbs if _matches(kb["name"], name) or _matches(kb["id"], name)]
    kbs.sort(key=lambda kb: (kb["name"], kb["id"]))
    return envelope(kbs, limit=limit, offset=offset)
