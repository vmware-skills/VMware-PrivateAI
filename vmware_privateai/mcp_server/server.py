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


from vmware_policy import describe_tool_parameters

# The docstrings in the tool modules imported above are the schema.
# `describe_tool_parameters` copies each `Args:` entry into the JSON schema an
# agent actually reads, and closes the object. Without it every parameter
# reaches the model as a bare name and a type, which is how a wrong guess
# becomes an unfiltered result or a silent zero-row answer instead of an error
# (real-hardware round, 2026-08-30). It runs here, after the imports that
# register the tools, because there is nothing to describe before them.
_DESCRIBED_PARAMS = describe_tool_parameters(mcp._tool_manager._tools)


def main() -> None:
    """Entry point for the ``vmware-privateai-mcp`` / ``vmware-privateai mcp`` command."""
    mcp.run()


if __name__ == "__main__":
    main()
