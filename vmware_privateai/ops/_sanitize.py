"""Prompt-injection defense for API / vCenter text that reaches the agent.

Mandatory family rule: every field carrying attacker- or operator-authorable text
from the vSphere API or a PAIS REST response (device/vendor/VM names, vGPU profile
strings, PAIS model ids and knowledge-base descriptions) must be sanitized at
projection time before it enters an LLM transcript — a KB description is the highest-
value injection surface in this skill.

Delegates to the family-consolidated ``vmware_policy.sanitize.sanitize`` (one
implementation, not a 23rd private copy) and exposes it as ``_sanitize`` so ops
projections read uniformly. Truncates to <=500 chars and strips C0/C1 control
characters + Unicode format chars; ``None`` degrades to "" (踩坑 形态 #1).
"""

from __future__ import annotations

from vmware_policy.sanitize import sanitize as _policy_sanitize


def _sanitize(text: object, max_len: int = 500) -> str:
    """Strip control/format chars and truncate untrusted API text to ``max_len``.

    Any input is coerced to ``str``; ``None`` and blank degrade to "".
    """
    if text is None:
        return ""
    return _policy_sanitize(str(text), max_len=max_len)
