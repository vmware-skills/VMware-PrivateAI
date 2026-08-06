"""Local CLI audit log (JSON Lines).

MCP write operations are audited to ~/.vmware/audit.db via vmware-policy's
@vmware_tool decorator; this logger is the CLI-side JSON-Lines companion. Audit
write failure must NEVER block the main operation — it degrades to a stderr
warning and the operation continues.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_log = logging.getLogger("vmware-privateai.audit")

DEFAULT_LOG = Path.home() / ".vmware-privateai" / "audit.log"


class AuditLogger:
    """Append-only JSON-Lines audit log for CLI write operations."""

    def __init__(self, log_file: Path | None = None) -> None:
        self._log_file = log_file or DEFAULT_LOG

    def record(
        self,
        *,
        target: str,
        operation: str,
        resource: str,
        parameters: dict[str, Any] | None = None,
        result: str = "ok",
    ) -> None:
        """Append one audit entry. Never raises — degrades to stderr on failure."""
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "target": target,
            "operation": operation,
            "resource": resource,
            "parameters": parameters or {},
            "result": result,
        }
        try:
            self._log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            print(f"[audit] warning: could not write audit log: {exc}", file=sys.stderr)
            _log.warning("audit write failed: %s", exc)
