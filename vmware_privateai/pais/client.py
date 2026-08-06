"""HTTP client for the Private AI Service (PAIS) REST API.

A thin httpx wrapper that adds OIDC/OAuth2 bearer auth and translates every HTTP / transport
error into a teaching :class:`PaisError` at ONE place (``_request``) — 踩坑 #37: REST-wrapper
skills must centralize error translation in the connection layer, not per-function, so a bad
token or a 404 surfaces an actionable message instead of a raw traceback. All ops go through
this client; ops code never calls httpx directly.
"""

from __future__ import annotations

from typing import Any

import httpx

from vmware_privateai.ops._errors import PaisError
from vmware_privateai.pais.config import TOKEN_ENV_VAR, PaisConfig, load_pais_config

_TIMEOUT = 30.0
# Transient gateway statuses that earn one light retry (family error-recovery layer 1).
_TRANSIENT = frozenset({502, 503, 504})


class PaisClient:
    """A configured, bearer-authenticated PAIS REST client. Use as a context manager."""

    def __init__(self, config: PaisConfig, *, transport: httpx.BaseTransport | None = None) -> None:
        self._config = config
        self._client = httpx.Client(
            base_url=config.endpoint.rstrip("/"),
            verify=config.verify_ssl,
            timeout=_TIMEOUT,
            transport=transport,
        )

    @classmethod
    def from_config(cls, config: PaisConfig | None = None) -> PaisClient:
        return cls(config or load_pais_config())

    def __enter__(self) -> PaisClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def get_json(self, path: str) -> Any:
        """GET ``path`` and return decoded JSON, or raise a teaching PaisError."""
        return self._request("GET", path)

    def _request(self, method: str, path: str) -> Any:
        # Reading the token here (a property) picks up a rotated token on every call and
        # keeps the (missing-token -> ConfigError) teaching message on the same code path.
        headers = {"Authorization": f"Bearer {self._config.token}", "Accept": "application/json"}
        # One light retry on a transient transport / gateway error (502/503/504); 4xx and a
        # successful response are returned/raised immediately (review LOW — family layer 1).
        resp = None
        for attempt in range(2):
            try:
                resp = self._client.request(method, path, headers=headers)
            except httpx.TransportError as exc:
                if attempt == 0:
                    continue
                raise PaisError(
                    f"Could not reach the PAIS endpoint '{self._config.endpoint}' — check the "
                    f"'pais.endpoint' in config.yaml is reachable from this machine and that its "
                    f"TLS certificate is trusted (set pais.verify_ssl: false only for a self-signed lab)."
                ) from exc
            if resp.status_code in _TRANSIENT and attempt == 0:
                continue
            break

        code = resp.status_code
        if code in (401, 403):
            raise PaisError(
                f"PAIS rejected the request (HTTP {code}). The bearer token in {TOKEN_ENV_VAR} "
                f"is likely expired or lacks scope — obtain a fresh token from your Identity "
                f"Provider, re-export {TOKEN_ENV_VAR}, and retry."
            )
        if code == 404:
            raise PaisError(
                f"PAIS path not found (HTTP 404) at '{path}'. Confirm the PAIS base URL / namespace "
                f"in config.yaml 'pais.endpoint' — the /api/v1 path prefix is deployment-specific and "
                f"unconfirmed (INFERRED_EXACT), so a 404 usually means a base-URL mismatch, not a bug."
            )
        if code >= 400:
            raise PaisError(
                f"PAIS request failed (HTTP {code}) at '{path}'. Check the PAIS service health and "
                f"the request scope, then retry."
            )

        try:
            return resp.json()
        except ValueError as exc:
            raise PaisError(
                f"PAIS returned a non-JSON response (HTTP {code}) at '{path}'. The configured "
                f"'pais.endpoint' may be pointing at a proxy or login page rather than the API."
            ) from exc
