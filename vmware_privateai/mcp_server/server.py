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

from vmware_policy import (
    describe_tool_parameters,
)

from vmware_privateai.mcp_server._shared import mcp
from vmware_privateai.mcp_server.tools import (
    advisor,  # noqa: F401  (registers sizing / bundle advisor tools — no connection)
    gpu,  # noqa: F401  (registers GPU inventory + host-readiness tools)
    pais,  # noqa: F401  (registers PAIS model/KB/catalog/data-source/monitoring tools)
    vgpu_profiles,  # noqa: F401  (registers profile-catalog + profile-validate tools)
)

# The docstrings in the tool modules imported above are the schema.
# `describe_tool_parameters` copies each `Args:` entry into the JSON schema an
# agent actually reads, and closes the object. Without it every parameter
# reaches the model as a bare name and a type, which is how a wrong guess
# becomes an unfiltered result or a silent zero-row answer instead of an error
# (real-hardware round, 2026-08-30). It runs here, after the imports that
# register the tools, because there is nothing to describe before them.
_DESCRIBED_PARAMS = describe_tool_parameters(mcp._tool_manager._tools)


# The environment resolver lives in policy_environment so the CLI registers
# it too (its @guarded writes go through the same guard()); importing it here
# registers it for the MCP surface.
from vmware_privateai.policy_environment import _cached_config, _environment_for  # noqa: E402,F401

#: How long a stop signal waits for the session logout before exiting anyway.
#: A logout to a vCenter that answers takes well under a second.
_STOP_LOGOUT_SECONDS = 5.0


def _exit_on_stop_signals() -> None:
    """Turn the signals a client stops this server with into a normal exit.

    Claude Code stops a stdio MCP server with SIGINT and then SIGTERM about a
    millisecond later (measured 2026-09-15). Python's default SIGTERM ends the
    process on the spot, before ``atexit`` runs, so the ``Disconnect`` the
    connection layer registered never happened and every conversation left its
    vCenter session open. ``SystemExit`` is not enough: raised from the handler it unwinds the event
    loop, but interpreter shutdown then waits for anyio's worker thread blocked
    reading stdin, which the client keeps open, so ``atexit`` still never ran
    (independent review, 2026-09-15; a test driving the real stdio loop hung in
    all five skills). So the first stop signal ignores the rest, runs the
    ``atexit`` callbacks, and leaves with ``os._exit`` — nothing waits on that
    thread. The callbacks run on a worker thread with a deadline: pyVmomi
    connects with ``httpConnectionTimeout=None``, so a logout to a vCenter that
    stopped answering, or one waiting on the SOAP stub lock a tool call held
    when the signal landed, otherwise left a server that ignored every stop
    signal and only SIGKILL ended (second independent review, 2026-09-15).
    """
    import atexit
    import os
    import signal
    import threading

    stop_signals = [
        getattr(signal, name) for name in ("SIGINT", "SIGTERM", "SIGHUP") if hasattr(signal, name)
    ]

    def _stop(signum: int, _frame: object) -> None:
        for sig in stop_signals:
            signal.signal(sig, signal.SIG_IGN)
        try:
            logout = threading.Thread(
                target=atexit._run_exitfuncs, name="logout-on-stop", daemon=True
            )
            logout.start()
            logout.join(_STOP_LOGOUT_SECONDS)
            if logout.is_alive():
                # Non-blocking, straight to fd 2: a client that keeps stderr open
                # but stops reading it would otherwise park this write, and the
                # exit, on a full pipe (independent review, 2026-09-15). A
                # message that cannot be written is dropped — exiting matters more.
                message = (
                    f"Session logout did not finish within {_STOP_LOGOUT_SECONDS:.0f}s; "
                    "exiting without it. vCenter or ESXi ends the session when it "
                    "idles out.\n"
                )
                try:
                    os.set_blocking(2, False)
                    os.write(2, message.encode("utf-8", "replace"))
                except OSError:
                    pass
        finally:
            os._exit(128 + signum)

    for sig in stop_signals:
        signal.signal(sig, _stop)


def main() -> None:
    """Entry point for the ``vmware-privateai-mcp`` / ``vmware-privateai mcp`` command."""
    _exit_on_stop_signals()
    mcp.run()


if __name__ == "__main__":
    main()
