"""MCP server for vmware-privateai (stdio transport).

Namespaced entry point ``vmware_privateai.mcp_server.server:main`` — never a
top-level ``mcp_server`` (踩坑 #41: colliding top-level module names silently
overwrite each other when two skills are installed in one environment).

The ``mcp`` instance is defined in ``_shared`` (tool modules import it from there);
this module imports the tool modules so their ``@mcp.tool`` functions register.
Tool signatures use ``Optional[X]``, never PEP 604 ``X | None`` (踩坑 #33) —
FastMCP/Pydantic reflect them at import time.
"""

from __future__ import annotations

from vmware_privateai.mcp_server._shared import mcp
from vmware_privateai.mcp_server.tools import (
    advisor,  # noqa: F401  (registers sizing / bundle advisor tools — no connection)
    gpu,  # noqa: F401  (registers GPU inventory + host-readiness tools)
    pais,  # noqa: F401  (registers PAIS model/KB/catalog/data-source/monitoring tools)
    vgpu_profiles,  # noqa: F401  (registers profile-catalog + profile-validate tools)
)


from vmware_privateai.config import CONFIG_FILE, load_config
from vmware_policy import (
    describe_tool_parameters,
    mtime_cached_loader,
    set_environment_resolver,
)

# The docstrings in the tool modules imported above are the schema.
# `describe_tool_parameters` copies each `Args:` entry into the JSON schema an
# agent actually reads, and closes the object. Without it every parameter
# reaches the model as a bare name and a type, which is how a wrong guess
# becomes an unfiltered result or a silent zero-row answer instead of an error
# (real-hardware round, 2026-08-30). It runs here, after the imports that
# register the tools, because there is nothing to describe before them.
_DESCRIBED_PARAMS = describe_tool_parameters(mcp._tool_manager._tools)


# ── environment resolver ─────────────────────────────────────────────────────
#
# Policy rules scope by environment ("irreversible work in production needs a
# second person"), and vmware_policy cannot read this skill's config itself —
# registering this lookup is what lets those rules fire at all. Without it every
# target reads as undeclared and no environment-scoped rule ever matches.
#
# This skill's config has carried `environment_for` since it shipped; the
# registration was simply never wired, and the family gate that should have
# caught it did not list this repo. Both are fixed together (2026-08-30).
_cached_config = mtime_cached_loader("VMWARE_PRIVATEAI_CONFIG", CONFIG_FILE, load_config)


def _environment_for(target: str | None) -> str:
    """The environment label for ``target``, or "" when it cannot be read.

    An unreadable config means *undeclared*, not *production*: guessing the
    strict label here would refuse work the operator never scoped, and guessing
    the loose one would be the fail-open this family keeps finding. Undeclared
    is the honest answer and the one vmware_policy documents.
    """
    try:
        return _cached_config().environment_for(target)
    except Exception:  # noqa: BLE001 — an unreadable config means "undeclared"
        return ""


set_environment_resolver(_environment_for)


def main() -> None:
    """Entry point for the ``vmware-privateai-mcp`` / ``vmware-privateai mcp`` command."""
    mcp.run()


if __name__ == "__main__":
    main()
