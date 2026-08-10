"""Regression tests for the Private AI Service (PAIS) vertical.

Covers the anti-phantom-endpoint gate (踩坑 #36: the exact REST paths the code hits must be
spec-listed), centralized HTTP-error translation into teaching PaisErrors (踩坑 #37), the
missing-token teaching error, and defensive parsing of an unconfirmed response shape
(踩坑 形态 #1: both a bare list and an enveloped body, absent fields degrade).
"""

from __future__ import annotations

import importlib.util
import pathlib

import httpx
import pytest

from vmware_privateai.config import ConfigError
from vmware_privateai.ops import pais
from vmware_privateai.ops._errors import PaisError
from vmware_privateai.pais.client import PaisClient
from vmware_privateai.pais.config import TOKEN_ENV_VAR, PaisConfig


def _load_spec():
    path = pathlib.Path(__file__).parent.parent / "spec" / "privateai_endpoints.py"
    spec = importlib.util.spec_from_file_location("privateai_endpoints", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _client(monkeypatch, handler):
    monkeypatch.setenv(TOKEN_ENV_VAR, "test-token")
    cfg = PaisConfig(endpoint="https://pais.test", verify_ssl=False)
    return PaisClient(cfg, transport=httpx.MockTransport(handler))


# --- anti-phantom-endpoint gate -------------------------------------------------


def test_pais_paths_and_keys_are_spec_listed():
    spec = _load_spec()
    assert pais.PAIS_KEYS_USED - set(spec.PAIS_ENDPOINTS) == set()
    allowed = {e["path"] for e in spec.PAIS_ENDPOINTS.values()}
    assert pais.PAIS_PATHS_USED - allowed == set(), "ops/pais touches a PAIS path absent from the verified spec"


def test_ops_hits_exactly_the_spec_model_path(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"object": "list", "data": []})

    with _client(monkeypatch, handler) as client:
        pais.list_models(client)
    assert seen["path"] == "/api/v1/compatibility/openai/v1/models"  # executable spec-path check
    assert seen["auth"] == "Bearer test-token"


# --- response parsing (unconfirmed shape → defensive) --------------------------


def test_list_models_parses_openai_envelope(monkeypatch):
    body = {"object": "list", "data": [{"id": "llama-3-8b", "owned_by": "meta"}, {"id": "mixtral"}]}
    with _client(monkeypatch, lambda r: httpx.Response(200, json=body)) as client:
        out = pais.list_models(client)
    assert out["total"] == 2
    assert out["items"][0] == {"id": "llama-3-8b", "owned_by": "meta", "created": None}


def test_list_models_parses_bare_list_and_filters(monkeypatch):
    body = [{"id": "llama-3-8b"}, {"id": "mixtral-8x7b"}]
    with _client(monkeypatch, lambda r: httpx.Response(200, json=body)) as client:
        out = pais.list_models(client, name="mixtral")
    assert out["total"] == 1 and out["items"][0]["id"] == "mixtral-8x7b"


def test_list_models_unexpected_shape_degrades_to_empty(monkeypatch):
    # 踩坑 形态 #1: an unexpected body must read as checked-and-none, not crash.
    with _client(monkeypatch, lambda r: httpx.Response(200, json={"unexpected": True})) as client:
        out = pais.list_models(client)
    assert out["items"] == [] and out["truncated"] is False


def test_list_knowledge_bases_parses_and_projects(monkeypatch):
    body = {"knowledge_bases": [{"id": "kb1", "name": "docs", "status": "ready"}]}
    with _client(monkeypatch, lambda r: httpx.Response(200, json=body)) as client:
        out = pais.list_knowledge_bases(client)
    assert out["total"] == 1
    assert out["items"][0]["name"] == "docs" and out["items"][0]["status"] == "ready"


def test_model_catalog_hits_spec_path_and_projects(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={"models": [{"id": "llama-70b", "status": "approved", "source": "hf"}]})

    with _client(monkeypatch, handler) as client:
        out = pais.list_model_catalog(client)
    assert seen["path"] == "/api/v1/control/models"  # executable spec-path check (INFERRED)
    assert out["total"] == 1
    assert out["items"][0]["id"] == "llama-70b" and out["items"][0]["status"] == "approved"


def test_model_catalog_unexpected_shape_degrades_to_empty(monkeypatch):
    with _client(monkeypatch, lambda r: httpx.Response(200, json={"nope": 1})) as client:
        out = pais.list_model_catalog(client)
    assert out["items"] == [] and out["truncated"] is False


def test_data_sources_hits_spec_path_and_projects(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        body = {"data_sources": [{"id": "ds1", "name": "sharepoint", "type": "web", "status": "ok"}]}
        return httpx.Response(200, json=body)

    with _client(monkeypatch, handler) as client:
        out = pais.list_data_sources(client, name="share")
    assert seen["path"] == "/api/v1/control/data-sources"
    assert out["total"] == 1 and out["items"][0]["type"] == "web"


def test_model_catalog_404_gives_base_url_teaching_error(monkeypatch):
    with _client(monkeypatch, lambda r: httpx.Response(404, text="nope")) as client:
        with pytest.raises(PaisError) as ei:
            pais.list_model_catalog(client)
    assert "endpoint" in str(ei.value).lower()  # INFERRED path 404 -> base-URL teaching, not a bug


# --- centralized error translation (踩坑 #37) ----------------------------------


def test_401_raises_token_teaching_error(monkeypatch):
    with _client(monkeypatch, lambda r: httpx.Response(401, json={})) as client:
        with pytest.raises(PaisError) as ei:
            pais.list_models(client)
    assert TOKEN_ENV_VAR in str(ei.value)


def test_404_raises_base_url_teaching_error(monkeypatch):
    with _client(monkeypatch, lambda r: httpx.Response(404, text="nope")) as client:
        with pytest.raises(PaisError) as ei:
            pais.list_knowledge_bases(client)
    assert "endpoint" in str(ei.value).lower()


def test_transport_error_raises_teaching_error(monkeypatch):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with _client(monkeypatch, boom) as client:
        with pytest.raises(PaisError) as ei:
            pais.list_models(client)
    assert "reach" in str(ei.value).lower()


def test_non_json_raises_teaching_error(monkeypatch):
    with _client(monkeypatch, lambda r: httpx.Response(200, text="<html>login</html>")) as client:
        with pytest.raises(PaisError):
            pais.list_models(client)


# --- transient-error retry (review LOW — family error-recovery layer 1) --------


def test_transient_503_is_retried_once_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="gateway busy")
        return httpx.Response(200, json={"object": "list", "data": [{"id": "m1"}]})

    with _client(monkeypatch, handler) as client:
        out = pais.list_models(client)
    assert calls["n"] == 2 and out["total"] == 1  # one retry, second attempt succeeded


def test_persistent_503_surfaces_after_one_retry(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, text="down")

    with _client(monkeypatch, lambda r: handler(r)) as client:
        with pytest.raises(PaisError):
            pais.list_models(client)
    assert calls["n"] == 2  # tried twice (one retry) then surfaced — no infinite loop


# --- missing token --------------------------------------------------------------


def test_missing_token_raises_config_error(monkeypatch):
    monkeypatch.delenv(TOKEN_ENV_VAR, raising=False)
    cfg = PaisConfig(endpoint="https://pais.test", verify_ssl=False)
    client = PaisClient(cfg, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[])))
    with client:
        with pytest.raises(ConfigError) as ei:
            pais.list_models(client)
    assert TOKEN_ENV_VAR in str(ei.value)
