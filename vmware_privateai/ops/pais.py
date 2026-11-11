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
from urllib.parse import urlencode

from vmware_privateai.ops._paging import envelope
from vmware_privateai.ops._errors import PaisError
from vmware_privateai.ops._sanitize import _sanitize
from vmware_privateai.pais.client import PaisClient

# Exact REST path strings this module touches — asserted ⊆ spec PAIS paths by the gate (踩坑 #36).
_MODELS_PATH = "/api/v1/compatibility/openai/v1/models"
_KNOWLEDGE_BASES_PATH = "/api/v1/control/knowledge-bases"
_MODEL_CATALOG_PATH = "/api/v1/control/models"
_DATA_SOURCES_PATH = "/api/v1/control/data-sources"
_MCP_SERVERS_PATH = "/api/v1/control/mcp-servers"
_MCP_TOOLS_PATH = "/api/v1/control/mcp-servers/tools"
PAIS_PATHS_USED: frozenset[str] = frozenset(
    {_MODELS_PATH, _KNOWLEDGE_BASES_PATH, _MODEL_CATALOG_PATH, _DATA_SOURCES_PATH,
     _MCP_SERVERS_PATH, _MCP_TOOLS_PATH}
)
# Spec keys — asserted ⊆ PAIS_ENDPOINTS by the gate.
PAIS_KEYS_USED: frozenset[str] = frozenset(
    {"list_models", "list_knowledge_bases", "list_model_catalog", "list_data_sources",
     "list_pais_mcp_servers", "list_pais_mcp_tools"}
)


def _gallery_query(limit: int, after: str | None, **filters: object) -> str:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if after is not None and (not isinstance(after, str) or not 1 <= len(after) <= 128):
        raise ValueError("after must be a nonempty cursor of at most 128 characters")
    params: list[tuple[str, str]] = [("limit", str(limit))]
    if after is not None:
        params.append(("after", after))
    for key, value in filters.items():
        if value is not None:
            params.append((key, str(value).lower() if isinstance(value, bool) else str(value)))
    return urlencode(params)


def _gallery_page(payload: Any, limit: int, project, after: str | None = None) -> dict:
    """Keep PAIS cursor semantics; malformed bodies cannot mean an empty fleet."""
    if (not isinstance(payload, dict) or not isinstance(payload.get("data"), list)
            or not all(isinstance(row, dict) for row in payload["data"])
            or not isinstance(payload.get("has_more"), bool)):
        return {"items": [], "returned": 0, "limit": limit, "total": None,
                "truncated": None, "next_after": None, "status": "unmeasured",
                "hint": "PAIS MCP gallery response shape was not recognized; no empty-inventory conclusion."}
    rows = [project(row) for row in payload["data"][:limit]]
    over_limit = len(payload["data"]) > limit
    has_more = payload["has_more"] or over_limit
    # An appliance that ignores limit may return a cursor beyond the displayed
    # rows. Do not offer a cursor that would silently skip those records.
    cursor = payload.get("last_id") if has_more and not over_limit else None
    if (not isinstance(cursor, str) or not cursor or cursor == after
            or len(cursor) > 128 or any(ord(c) < 32 or ord(c) == 127 for c in cursor)
            or _sanitize(cursor, 128) != cursor):
        # Opaque cursors cannot be trimmed or sanitized into different tokens.
        # Re-offering the incoming cursor would make callers repeat one page.
        cursor = None
    # PAIS only populates num_objects when requested; avoid a false zero.
    return {"items": rows, "returned": len(rows), "limit": limit, "total": None,
            "truncated": has_more, "next_after": cursor,
            "status": "measured" if not has_more or cursor else "partial",
            "hint": ("PAIS reports more entries; pass next_after to continue."
                     if cursor else "PAIS reports more entries but returned no usable advancing cursor; listing is incomplete."
                     if has_more else None),
            "scope_note": (
                "PAIS tool approval is not backend authorization; "
                "VMware Policy and product RBAC still apply."
            )}


def list_pais_mcp_servers(client: PaisClient, *, limit: int = 50, after: str | None = None) -> dict:
    """List registered PAIS MCP servers without returning URLs or headers."""
    payload = client.get_json(f"{_MCP_SERVERS_PATH}?{_gallery_query(limit, after)}")

    def project(row: dict) -> dict:
        return {key: _sanitize(row.get(key), 160) for key in
                ("id", "name", "transport", "availability")}

    return _gallery_page(payload, limit, project, after)


def list_pais_mcp_tools(
    client: PaisClient, *, server_id: str | None = None,
    is_approved: bool | None = None, limit: int = 50, after: str | None = None,
) -> dict:
    """List PAIS gallery tool names and approval state, excluding raw schemas."""
    if server_id is not None and (not isinstance(server_id, str) or not 1 <= len(server_id) <= 128):
        raise ValueError("server_id must be a nonempty ID of at most 128 characters")
    if is_approved is not None and not isinstance(is_approved, bool):
        raise ValueError("is_approved must be true, false or null")
    payload = client.get_json(
        f"{_MCP_TOOLS_PATH}?{_gallery_query(limit, after, server=server_id, is_approved=is_approved)}"
    )

    def project(row: dict) -> dict:
        server = row.get("server") if isinstance(row.get("server"), dict) else {}
        approved = row.get("is_approved")
        linked = row.get("num_linked_agents")
        return {"id": _sanitize(row.get("id"), 160),
                "server_id": _sanitize(server.get("id"), 160),
                "server_name": _sanitize(server.get("name"), 160),
                "name": _sanitize(row.get("origin_name"), 160),
                "is_approved": approved if isinstance(approved, bool) else None,
                "availability": _sanitize(row.get("availability"), 80),
                "linked_agents": (
                    linked if isinstance(linked, int) and not isinstance(linked, bool) and linked >= 0
                    else None
                )}

    return _gallery_page(payload, limit, project, after)


def _as_rows(payload: Any, *keys: str) -> list:
    """Read a recognized collection; unknown evidence is never checked-and-none."""
    rows = payload if isinstance(payload, list) else None
    if isinstance(payload, dict):
        for key in keys:
            if key in payload:
                rows = payload[key]
                break
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise PaisError(
            "PAIS collection response shape was not recognized; inventory is unknown. "
            "Check the configured endpoint and obtain a redacted API response; "
            "do not interpret this as an empty inventory."
        )
    return rows


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


def _project_catalog_model(m: Any) -> dict:
    m = m if isinstance(m, dict) else {}
    # Every string is operator/vendor free text reaching the agent — sanitize (injection surface).
    return {
        "id": _sanitize(m.get("id", "") or m.get("name", "") or ""),
        "name": _sanitize(m.get("name", "") or ""),
        "status": _sanitize(m.get("status", "") or m.get("state", "") or ""),
        "source": _sanitize(m.get("source", "") or m.get("repository", "") or ""),
    }


def list_model_catalog(client: PaisClient, *, name: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    """List the PAIS model catalog — models available/approved to DEPLOY (not the served ``/models``).

    Answers "which models are approved / what can I deploy in-house" (the air-gapped model-install
    question). Path ``/api/v1/control/models`` is INFERRED (unconfirmed against a live OpenAPI) —
    a 404 is translated by the client into a base-URL teaching message, never read as a bug.
    """
    payload = client.get_json(_MODEL_CATALOG_PATH)
    models = [_project_catalog_model(m) for m in _as_rows(payload, "models", "items", "data")]
    models = [m for m in models if _matches(m["id"], name) or _matches(m["name"], name)]
    models.sort(key=lambda m: (m["name"], m["id"]))
    return envelope(models, limit=limit, offset=offset)


def _project_data_source(d: Any) -> dict:
    d = d if isinstance(d, dict) else {}
    return {
        "id": _sanitize(d.get("id", "") or d.get("name", "") or ""),
        "name": _sanitize(d.get("name", "") or ""),
        "type": _sanitize(d.get("type", "") or d.get("kind", "") or ""),
        "status": _sanitize(d.get("status", "") or d.get("state", "") or ""),
    }


def list_data_sources(client: PaisClient, *, name: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    """List PAIS RAG data sources (ingest connectors feeding knowledge bases). Paginated.

    Path ``/api/v1/control/data-sources`` is INFERRED_EXACT (already in the spec index); a 404
    prompts a base-URL check, not a bug report (踩坑 #36).
    """
    payload = client.get_json(_DATA_SOURCES_PATH)
    rows = _as_rows(payload, "data_sources", "dataSources", "items", "data")
    sources = [_project_data_source(d) for d in rows]
    sources = [s for s in sources if _matches(s["name"], name) or _matches(s["id"], name)]
    sources.sort(key=lambda s: (s["name"], s["id"]))
    return envelope(sources, limit=limit, offset=offset)
