"""Private AI Service (PAIS) MCP tools (2 read) — served models and knowledge bases.

pais_model_list, pais_knowledge_base_list [READ]. These talk to the PAIS REST endpoint
(separate from the vCenter connection), authenticated with the OIDC bearer token in
VMWARE_PRIVATEAI_PAIS_TOKEN. Signatures use ``Optional[X]`` (not PEP 604) — 踩坑 #33.
"""

from __future__ import annotations

from typing import Optional

from vmware_policy import vmware_tool

from vmware_privateai.mcp_server._shared import _get_connection, _safe_error, mcp

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


@mcp.tool(annotations=_READ)
@vmware_tool(risk_level="low")
def pais_model_catalog(
    name: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """[READ] List the PAIS model CATALOG — models available/approved to deploy (not the served set).

    Answers "which models are approved / what can I install in-house" (the air-gapped model
    question). Each item has id, name, status, source. Distinct from pais_model_list (which lists
    what is already SERVED). Path is INFERRED — a 404 returns a base-URL teaching message, not a
    bug. Needs the PAIS endpoint + VMWARE_PRIVATEAI_PAIS_TOKEN.

    Args:
        name: Substring-match the model id/name.
        limit: Page size (default 50).
        offset: Page offset.
    """
    try:
        from vmware_privateai.ops.pais import list_model_catalog
        from vmware_privateai.pais.client import PaisClient

        with PaisClient.from_config() as client:
            return list_model_catalog(client, name=name, limit=limit, offset=offset)
    except Exception as exc:  # noqa: BLE001
        return {"error": _safe_error(exc, "pais_model_catalog")}


@mcp.tool(annotations=_READ)
@vmware_tool(risk_level="low")
def pais_data_source_list(
    name: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """[READ] List PAIS RAG data sources (ingest connectors that feed knowledge bases).

    Each item has id, name, type, status. Pair with pais_knowledge_base_list to see the RAG
    pipeline. Path is INFERRED — a 404 prompts a base-URL check, not a bug. Needs the PAIS
    endpoint + VMWARE_PRIVATEAI_PAIS_TOKEN.

    Args:
        name: Substring-match the data-source name/id.
        limit: Page size (default 50).
        offset: Page offset.
    """
    try:
        from vmware_privateai.ops.pais import list_data_sources
        from vmware_privateai.pais.client import PaisClient

        with PaisClient.from_config() as client:
            return list_data_sources(client, name=name, limit=limit, offset=offset)
    except Exception as exc:  # noqa: BLE001
        return {"error": _safe_error(exc, "pais_data_source_list")}


@mcp.tool(annotations=_READ)
@vmware_tool(risk_level="low")
def pais_monitoring_summary(
    hot_pct: float = 90.0,
    top: int = 5,
    target: Optional[str] = None,
) -> dict:
    """[READ] Fleet GPU monitoring rollup for VCF Ops dashboards — utilisation, hot/idle, busiest.

    Aggregates the per-VM vGPU perf counters into one estate summary: vgpu_vms, reporting_vms,
    idle_vms, hot_vms, gpu_pct/mem_pct avg+max, temp_c_max, a per-profile tally, and the busiest
    `top` VMs. This is the vCenter-perf-counter view (not PAIS REST) — deep per-SM/MIG/power
    telemetry needs NVIDIA DCGM and is out of scope (scope_note says so). Requires a vCenter
    connection and the NVIDIA host driver.

    Args:
        hot_pct: gpu_pct at/above which a VM is counted "hot" (default 90).
        top: How many busiest VMs to include (default 5).
        target: vCenter/ESXi target from config.yaml; omit to use the default.
    """
    try:
        from vmware_privateai.ops.monitoring import pais_monitoring_summary as _summary

        return _summary(_get_connection(target), hot_pct=hot_pct, top=top)
    except Exception as exc:  # noqa: BLE001
        return {"error": _safe_error(exc, "pais_monitoring_summary")}
