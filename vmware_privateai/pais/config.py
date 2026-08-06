"""Configuration for the Private AI Service (PAIS) REST endpoint.

PAIS is configured SEPARATELY from vCenter targets: it is a per-namespace HTTP endpoint
with an OIDC/OAuth2 bearer token. The endpoint (base URL) lives in config.yaml under a
``pais:`` section; the bearer token is NEVER stored in a file — always via the
``VMWARE_PRIVATEAI_PAIS_TOKEN`` environment variable (short-lived, obtained from the IdP).

    pais:
      endpoint: https://pais.example.com   # base URL; the /api/v1 prefix is added by the client
      verify_ssl: true
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from vmware_privateai.config import CONFIG_FILE, ENV_FILE, ConfigError, _decode_secret

TOKEN_ENV_VAR = "VMWARE_PRIVATEAI_PAIS_TOKEN"


@dataclass(frozen=True)
class PaisConfig:
    """A Private AI Service endpoint (model serving / knowledge bases)."""

    endpoint: str
    verify_ssl: bool = True

    @property
    def token(self) -> str:
        """Bearer token from ``VMWARE_PRIVATEAI_PAIS_TOKEN`` (a ``b64:`` value is decoded).

        Resolved on every access so a rotating token sidecar is always picked up. Raises a
        teaching ConfigError (an OSError subclass both the CLI and MCP error paths pass
        through verbatim) when the variable is unset.
        """
        tok = os.environ.get(TOKEN_ENV_VAR, "")
        if not tok:
            raise ConfigError(
                f"PAIS bearer token not found. Set environment variable {TOKEN_ENV_VAR} "
                f"(or add {TOKEN_ENV_VAR}=<token> to {ENV_FILE}, chmod 600) with a token "
                f"obtained from your Identity Provider, then re-run."
            )
        return _decode_secret(tok)


def load_pais_config(config_path: Path | None = None) -> PaisConfig:
    """Load the ``pais:`` section from config.yaml.

    Raises FileNotFoundError if config.yaml is missing, or a teaching ConfigError if no
    ``pais.endpoint`` is configured — so a PAIS tool used without setup routes the operator
    to fix config rather than dumping a traceback.
    """
    path = config_path or CONFIG_FILE
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            f"Create it and add a 'pais:' section with an 'endpoint:' (the Private AI Service "
            f"base URL) to use the model-serving / knowledge-base tools, then set {TOKEN_ENV_VAR} "
            f"in {ENV_FILE} (chmod 600)."
        )
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    pais = raw.get("pais") or {}
    endpoint = str(pais.get("endpoint", "") or "").strip()
    if not endpoint:
        raise ConfigError(
            f"No PAIS endpoint configured. Add a 'pais:' section with an 'endpoint:' "
            f"(the Private AI Service base URL) to {CONFIG_FILE}, and set {TOKEN_ENV_VAR} "
            f"with a bearer token, then re-run."
        )
    return PaisConfig(endpoint=endpoint, verify_ssl=bool(pais.get("verify_ssl", True)))
