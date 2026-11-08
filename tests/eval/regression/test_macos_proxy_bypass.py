"""System proxy bypass must not override an explicitly supplied PAIS transport."""

import urllib.request

import httpx

from vmware_privateai.pais.client import PaisClient
from vmware_privateai.pais.config import PaisConfig


def test_pais_system_proxy_bypass_keeps_endpoint_path_and_tls_options(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("VMWARE_PRIVATEAI_PAIS_TOKEN", "test-token")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: host == "target.invalid")
    options, seen = [], []

    def response(request):
        seen.append(request)
        return httpx.Response(200, json={"items": []})

    def transport(**kwargs):
        options.append(kwargs)
        return httpx.MockTransport(response)

    monkeypatch.setattr(httpx, "HTTPTransport", transport)
    with PaisClient(PaisConfig(endpoint="https://target.invalid/pais", verify_ssl=False)) as client:
        assert client.get_json("/api/v1/models") == {"items": []}
    assert options == [{"verify": False, "trust_env": True}]
    assert seen[0].headers["Authorization"] == "Bearer test-token"


def test_explicit_pais_transport_is_preserved(monkeypatch):
    monkeypatch.setenv("VMWARE_PRIVATEAI_PAIS_TOKEN", "test-token")
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: True)

    def unexpected(**kwargs):
        raise AssertionError("direct mount would override the explicit transport")

    monkeypatch.setattr(httpx, "HTTPTransport", unexpected)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"items": []}))
    with PaisClient(PaisConfig(endpoint="https://target.invalid"), transport=transport) as client:
        assert client.get_json("/api/v1/models") == {"items": []}
