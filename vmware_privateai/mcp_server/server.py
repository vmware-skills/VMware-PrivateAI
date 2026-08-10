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


def main() -> None:
    """Entry point for the ``vmware-privateai-mcp`` / ``vmware-privateai mcp`` command."""
    mcp.run()


if __name__ == "__main__":
    main()
