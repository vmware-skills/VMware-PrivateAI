"""Private AI Service (PAIS) MCP tools (2 read) — served models and knowledge bases.

pais_model_list, pais_knowledge_base_list [READ]. These talk to the PAIS REST endpoint
(separate from the vCenter connection), authenticated with the OIDC bearer token in
VMWARE_PRIVATEAI_PAIS_TOKEN. Signatures use ``Optional[X]`` (not PEP 604) — 踩坑 #33.
"""

from __future__ import annotations

from typing import Optional

from vmware_policy import vmware_tool

from vmware_privateai.mcp_server._shared import _safe_error, mcp

_READ = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}


@mcp.tool(annotations=_READ)
@vmware_tool(risk_level="low")
def pais_model_list(
    name: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """[READ] List models served by Private AI Service (OpenAI-compatible /models).

    Returns a {items, returned, limit, total, truncated, hint} envelope; each item has id,
    owned_by, created. Needs a 'pais:' endpoint in config.yaml and a bearer token in
    VMWARE_PRIVATEAI_PAIS_TOKEN — a missing one returns a teaching error, not a traceback.

    Args:
        name: Substring-match the model id.
        limit: Page size (default 50).
        offset: Page offset.
    """
    try:
        from vmware_privateai.ops.pais import list_models
        from vmware_privateai.pais.client import PaisClient

        with PaisClient.from_config() as client:
            return list_models(client, name=name, limit=limit, offset=offset)
    except Exception as exc:  # noqa: BLE001
        return {"error": _safe_error(exc, "pais_model_list")}


@mcp.tool(annotations=_READ)
@vmware_tool(risk_level="low")
def pais_knowledge_base_list(
    name: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """[READ] List Private AI Service knowledge bases (RAG vector stores).

    Each item has id, name, status, description. Same PAIS endpoint + bearer token as
    pais_model_list. Paginated envelope; an empty items with truncated=false means the
    endpoint has no knowledge bases (checked-and-none), not a failure.

    Args:
        name: Substring-match the knowledge-base name or id.
        limit: Page size (default 50).
        offset: Page offset.
    """
    try:
        from vmware_privateai.ops.pais import list_knowledge_bases
        from vmware_privateai.pais.client import PaisClient

        with PaisClient.from_config() as client:
            return list_knowledge_bases(client, name=name, limit=limit, offset=offset)
    except Exception as exc:  # noqa: BLE001
        return {"error": _safe_error(exc, "pais_knowledge_base_list")}
