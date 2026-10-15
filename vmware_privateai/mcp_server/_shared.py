"""Shared MCP plumbing for the vmware-privateai tool modules.

Tool functions live in ``vmware_privateai/mcp_server/tools/*.py`` grouped by domain
and register onto the single ``mcp`` instance defined here. This module must not
import from the tool packages (they import *from* here) to avoid a circular import.
"""

from __future__ import annotations

import logging
import ssl
import threading

from mcp.server.fastmcp import FastMCP

from vmware_privateai import __version__
from vmware_privateai.config import AppConfig, ConfigError, load_config
from vmware_privateai.connection import ConnectionManager
from vmware_privateai.notify.audit import AuditLogger
from vmware_privateai.ops._errors import PrivateAiError

logger = logging.getLogger("vmware_privateai.mcp_server")

_BASE_INSTRUCTIONS = (
    "VMware Private AI Foundation with NVIDIA on vSphere: inventory GPU hosts and devices, see which VMs "
    "consume a vGPU, read GPU utilization, list and assign vGPU profiles, and list Private AI Service "
    "(PAIS) served models and knowledge bases."
)

_TARGET_RULE = (
    " Choosing a target: every tool that reaches vSphere takes `target`. Choose it from what the user "
    "asked. A vCenter, the whole environment, a cluster, or GPU capacity across several hosts: a vcenter "
    "target. One ESXi host the user names: the vcenter target that manages it, because vCenter holds that "
    "host's GPU devices alongside the VMs consuming them; use the host's own esxi target only when the "
    "user asks about the host directly or no vCenter manages it. If the request does not say which, and "
    "targets of different types could answer differently, ask the user which one before querying. A "
    "result does not name the target it came from, so say in the answer which `target` you passed."
)


def _target_listing(cfg: AppConfig) -> str:
    """The configured targets as ``name (type, host[, default])`` — or why there are none.

    A config that loads with zero targets is *readable*, just empty: that is a
    fresh install before ``config.yaml has been written``. It needs a different remedy from a config
    that could not be read at all, so the two must not collapse into one sentence
    — and neither may collapse into silence.

    Called from inside the caller's ``try`` on purpose: ``AppConfig.default_target``
    raises when the target list is empty, so the empty case is answered here before
    that property is ever asked, and anything unforeseen still lands on the caller's
    fallback instead of stopping the server.
    """
    if not cfg.targets:
        # The documented default path. ``VMWARE_PRIVATEAI_CONFIG`` can point somewhere else;
        # ``vmware-privateai doctor`` reports which file it actually resolved, and this
        # one-line remedy does not try to restate that.
        return "none yet — add one under `targets:` in ~/.vmware-privateai/config.yaml"
    default_name = cfg.default_target.name
    return "; ".join(
        f"{t.name} ({t.type}, {t.host}{', default' if t.name == default_name else ''})"
        for t in cfg.targets
    )


def _target_instructions() -> str:
    """Server instructions that name the configured targets and how to choose one.

    Until this existed the client was never told which targets are configured, so it called every tool
    with no ``target`` and got whichever one the connection layer defaults to. Monitor shipped that way
    until 2026-09-15: with a standalone ESXi host first in the file, "how many VMs does the vCenter have"
    was answered from that host, confidently and wrongly. This skill has the same shape — "how many GPUs
    are there" read from one ESXi host and read from vCenter are different answers that look identical.

    The listing is built from the loaded config rather than written out as a sentence, so it cannot drift
    the day the operator edits their file. The default is marked by comparing against ``default_target``,
    which is what :meth:`ConnectionManager.connect` itself falls back to when ``target`` is omitted — not
    a guess about which entry comes first.

    Never raises: a missing or broken config must not stop the server from starting. The tools report
    that error themselves, each with its own remedy.
    """
    try:
        detail = _target_listing(load_config())
    except Exception as exc:  # noqa: BLE001 — instructions are advisory, startup is not
        # Say so *under the marker* rather than dropping the listing. Shown no
        # listing at all, a client cannot tell "this skill has no targets" from
        # "this skill could not read them", and the first reading is the one that
        # produces a confident answer about a system nobody chose. Only the
        # exception's type name is interpolated — its text quotes the config path.
        detail = f"could not be read ({type(exc).__name__}) — run `vmware-privateai doctor`"
    return f"{_BASE_INSTRUCTIONS} Configured targets: {detail}.{_TARGET_RULE}"


mcp = FastMCP("vmware-privateai", instructions=_target_instructions())

# FastMCP takes no version argument and leaves the lowlevel server's at
# None, which makes `initialize` answer with the MCP SDK's version rather
# than ours. Set it so a client can tell which release it is talking to.
mcp._mcp_server.version = __version__

# Legacy CLI-side audit logger for write tools. The authoritative sink is
# ~/.vmware/audit.db via @vmware_tool; this dual-writes for back-compat.
_audit = AuditLogger()

_manager: ConnectionManager | None = None
# Calls run on worker threads side by side (HLD §16 D-13(2)); two first calls
# racing here would each build a manager, and one manager's sessions would be
# orphaned — open logins nobody pools or closes.
_manager_lock = threading.Lock()


def _get_manager() -> ConnectionManager:
    """The process's one connection manager, built on first use."""
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = ConnectionManager.from_config()
        return _manager


def _get_connection(target: str | None):
    """Lazy connection-manager helper — one manager per process, sessions pooled per target.

    Returns a pyVmomi ServiceInstance for the target vCenter/ESXi.
    """
    return _get_manager().connect(target)


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
