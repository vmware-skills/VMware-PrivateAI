"""Shared MCP plumbing for the vmware-privateai tool modules.

Tool functions live in ``vmware_privateai/mcp_server/tools/*.py`` grouped by domain
and register onto the single ``mcp`` instance defined here. This module must not
import from the tool packages (they import *from* here) to avoid a circular import.
"""

from __future__ import annotations

import logging
import ssl

from mcp.server.fastmcp import FastMCP

from vmware_privateai import __version__
from vmware_privateai.config import ConfigError
from vmware_privateai.connection import ConnectionManager
from vmware_privateai.notify.audit import AuditLogger
from vmware_privateai.ops._errors import PrivateAiError

logger = logging.getLogger("vmware_privateai.mcp_server")

mcp = FastMCP("vmware-privateai")

# FastMCP takes no version argument and leaves the lowlevel server's at
# None, which makes `initialize` answer with the MCP SDK's version rather
# than ours. Set it so a client can tell which release it is talking to.
mcp._mcp_server.version = __version__

# Legacy CLI-side audit logger for write tools. The authoritative sink is
# ~/.vmware/audit.db via @vmware_tool; this dual-writes for back-compat.
_audit = AuditLogger()

_manager: ConnectionManager | None = None


def _get_connection(target: str | None):
    """Lazy connection-manager helper — one manager per process, session per target.

    Returns a pyVmomi ServiceInstance for the target vCenter/ESXi.
    """
    global _manager
    if _manager is None:
        _manager = ConnectionManager.from_config()
    return _manager.connect(target)


def _target_name(target: str | None) -> str:
    """Audit display name for a target (or 'default')."""
    return target or "default"


def _safe_error(exc: Exception, tool: str) -> str:
    """Agent-safe error stringifier: teaching messages pass through; else masked.

    A PrivateAiError (GPU/PAIS not-found, bad-enum), a ConfigError (missing
    config.yaml / unknown target), or a connection failure already carries an
    actionable, sanitized teaching message, surfaced verbatim. Any other
    exception is masked so vCenter/TLS internals never reach the transcript.
    """
    # ssl.SSLError (incl. SSLCertVerificationError) IS a ValueError subclass — mask it BEFORE
    # the ValueError pass-through, or the raw cert chain / _ssl.c internals leak into the agent
    # transcript (review H2 — the exact v1.8.5 family regression).
    if isinstance(exc, ssl.SSLError):
        logger.exception("TLS error in tool %s", tool)
        return f"{tool} failed: a TLS/certificate error occurred (see server log)."
    if isinstance(exc, (PrivateAiError, ConfigError, FileNotFoundError, ConnectionError, ValueError)):
        return str(exc)
    logger.exception("Unexpected error in tool %s", tool)
    return f"{tool} failed: an unexpected error occurred (see server log)."
