"""Teaching-error types for vmware-privateai ops.

Errors must teach the operator how to fix them (Anthropic tool-design standard):
name the resource, the likely cause, and the command to run next.
"""

from __future__ import annotations


class PrivateAiError(Exception):
    """Base class for operator-fixable vmware-privateai errors (safe to show an agent)."""


class GpuNotFoundError(PrivateAiError):
    """A host, GPU device, or vGPU profile could not be found on the target."""


class PaisError(PrivateAiError):
    """A Private AI Service (PAIS) REST call failed (model serving / knowledge base)."""
