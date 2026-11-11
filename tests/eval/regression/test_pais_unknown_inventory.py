from unittest.mock import Mock
import pytest
from vmware_privateai.ops import pais
from vmware_privateai.ops._errors import PaisError

@pytest.mark.parametrize("read", [pais.list_models, pais.list_knowledge_bases, pais.list_model_catalog, pais.list_data_sources])
def test_unrecognized_inventory_cannot_be_reported_as_measured_empty(read):
    client = Mock()
    client.get_json.return_value = {"error": "upstream body contains fixture-secret"}
    with pytest.raises(PaisError, match="response shape") as exc:
        read(client)
    assert "fixture-secret" not in str(exc.value)

@pytest.mark.parametrize("payload", [{"data": ["not-an-object"]}, {"data": None, "items": []}])
def test_invalid_primary_collection_is_not_hidden_by_empty_fallback(payload):
    client = Mock()
    client.get_json.return_value = payload
    with pytest.raises(PaisError):
        pais.list_models(client)

def test_verified_empty_inventory_remains_a_measured_empty_result():
    client = Mock()
    client.get_json.return_value = {"data": []}
    out = pais.list_models(client)
    assert out["items"] == [] and out["total"] == 0
