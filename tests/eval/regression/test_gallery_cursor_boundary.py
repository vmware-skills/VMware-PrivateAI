"""Opaque continuation tokens must never be altered or offered as progress twice."""

import httpx
import pytest
from vmware_privateai.ops import pais
from vmware_privateai.pais.client import PaisClient
from vmware_privateai.pais.config import TOKEN_ENV_VAR, PaisConfig


@pytest.mark.parametrize("operation", [pais.list_pais_mcp_servers, pais.list_pais_mcp_tools])
@pytest.mark.parametrize("cursor", ["same-page", "a\x00b", "a\nb", "x" * 129, "", 8])
def test_unusable_cursor_cannot_skip_rows_or_loop(monkeypatch, operation, cursor):
    monkeypatch.setenv(TOKEN_ENV_VAR, "fixture-token")

    def server(request):
        assert request.url.params["after"] == "same-page"
        return httpx.Response(200, json={"data": [{"id": "one"}], "has_more": True, "last_id": cursor})

    with PaisClient(PaisConfig(endpoint="https://pais.invalid"), transport=httpx.MockTransport(server)) as client:
        out = operation(client, after="same-page")
    assert out["returned"] == 1
    assert out["truncated"] is True
    assert out["status"] == "partial"
    assert out["next_after"] is None, "a modified or repeated token must not be offered to callers"
    assert out["total"] is None


def test_valid_opaque_cursor_survives_query_encoding(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV_VAR, "fixture-token")
    cursor = "opaque+/=%?&unicode-片"
    seen = []

    def server(request):
        seen.append(dict(request.url.params))
        if len(seen) == 1:
            return httpx.Response(200, json={"data": [{"id": "first"}], "has_more": True, "last_id": cursor})
        return httpx.Response(200, json={"data": [{"id": "second"}], "has_more": False})

    with PaisClient(PaisConfig(endpoint="https://pais.invalid"), transport=httpx.MockTransport(server)) as client:
        first = pais.list_pais_mcp_servers(client)
        second = pais.list_pais_mcp_servers(client, after=first["next_after"])
    assert first["next_after"] == cursor
    assert seen[1]["after"] == cursor
    assert second["items"][0]["id"] == "second"
    assert second["truncated"] is False and second["next_after"] is None
