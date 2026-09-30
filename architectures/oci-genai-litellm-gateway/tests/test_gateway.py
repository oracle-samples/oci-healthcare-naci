"""Configuration and callback checks for the actual LiteLLM Gateway."""

import copy
import sys
from types import ModuleType
from unittest.mock import Mock

from fastapi import HTTPException
import pytest
import yaml

from gateway.oci_auth import OCIInstancePrincipalCallback, RefreshingOCISigner


def request_data(model="basic"):
    return {
        "model": model,
        "messages": [{"role": "user", "content": "Synthetic prompt"}],
    }


class FakeInstanceSigner:
    def __init__(self):
        self.calls = []

    def __call__(self, request, enforce_content_headers=True):
        self.calls.append((request, enforce_content_headers))
        request["refreshed"] = True
        return request

    def do_request_sign(self, request, enforce_content_headers=True):
        raise AssertionError("Direct SDK signing would bypass token refresh")


def test_gateway_config_uses_router_aliases_and_both_scope_presidio():
    with open("gateway/config.yaml", encoding="utf-8") as source:
        config = yaml.safe_load(source)
    assert {item["model_name"] for item in config["model_list"]} == {"basic", "reasoning"}
    assert {item["litellm_params"]["model"] for item in config["model_list"]} == {
        "os.environ/BASIC_MODEL", "os.environ/REASONING_MODEL",
    }
    assert config["router_settings"]["routing_strategy"] == "simple-shuffle"
    assert config["router_settings"]["enable_pre_call_checks"] is True
    guardrail = config["guardrails"][0]["litellm_params"]
    assert guardrail["guardrail"] == "presidio"
    assert guardrail["default_on"] is True
    assert guardrail["presidio_filter_scope"] == "both"
    assert guardrail["pii_entities_config"]["MRN"] == "MASK"
    settings = config["litellm_settings"]
    assert settings["callbacks"] == ["gateway.oci_auth.oci_instance_principal"]
    assert settings["turn_off_message_logging"] is True
    assert settings["redact_messages_in_exceptions"] is True
    assert "completion_model" not in config["general_settings"]


def test_signer_adapter_refreshes_and_survives_litellm_copying():
    sdk_signer = FakeInstanceSigner()
    adapter = RefreshingOCISigner(sdk_signer)
    request = {}
    assert adapter.do_request_sign(request, enforce_content_headers=False) is request
    assert request["refreshed"] is True
    assert sdk_signer.calls == [(request, False)]
    assert copy.deepcopy(adapter) is adapter


@pytest.mark.asyncio
@pytest.mark.parametrize("model,tokens", [("basic", 512), ("reasoning", 4096)])
async def test_callback_injects_one_refreshing_instance_signer(monkeypatch, model, tokens):
    sdk_signer = FakeInstanceSigner()
    signers = ModuleType("oci.auth.signers")
    signers.InstancePrincipalsSecurityTokenSigner = Mock(return_value=sdk_signer)
    monkeypatch.setitem(sys.modules, "oci.auth.signers", signers)
    callback = OCIInstancePrincipalCallback()

    first = await callback.async_pre_call_hook(None, None, request_data(model), "acompletion")
    second = await callback.async_pre_call_hook(None, None, request_data(model), "acompletion")

    assert first["max_tokens"] == tokens
    assert first["stream"] is False
    assert first["oci_signer"] is second["oci_signer"]
    signers.InstancePrincipalsSecurityTokenSigner.assert_called_once_with()
    signed = {}
    first["oci_signer"].do_request_sign(signed)
    assert signed["refreshed"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override",
    [
        {"model": "oci/arbitrary"},
        {"stream": True},
        {"tools": [{"type": "function"}]},
        {"content_safety": {}},
        {"max_tokens": 513},
        {"messages": [{"role": "tool", "content": "synthetic"}]},
        {"messages": [{"role": "user", "content": [{"type": "text", "text": "synthetic"}]}]},
        {"messages": [{"role": "user", "content": "synthetic", "name": "caller"}]},
        {"messages": [{"role": "user", "content": "x" * 25001}] * 2},
    ],
)
async def test_callback_rejects_unscanned_or_unbounded_surfaces(override):
    callback = OCIInstancePrincipalCallback()
    data = request_data()
    data.update(override)
    with pytest.raises(HTTPException) as error:
        await callback.async_pre_call_hook(None, None, data, "acompletion")
    assert error.value.status_code == 422
    assert callback._signer is None


@pytest.mark.asyncio
async def test_callback_stops_when_instance_principal_is_unavailable(monkeypatch):
    signers = ModuleType("oci.auth.signers")
    signers.InstancePrincipalsSecurityTokenSigner = Mock(side_effect=RuntimeError("synthetic secret"))
    monkeypatch.setitem(sys.modules, "oci.auth.signers", signers)
    callback = OCIInstancePrincipalCallback()
    with pytest.raises(HTTPException) as error:
        await callback.async_pre_call_hook(None, None, request_data(), "acompletion")
    assert error.value.status_code == 503
    assert "synthetic secret" not in error.value.detail


@pytest.mark.asyncio
async def test_post_call_keeps_scanned_content_and_removes_reasoning_surfaces():
    callback = OCIInstancePrincipalCallback()
    response = {"choices": [{"message": {
        "role": "assistant",
        "content": "Safe synthetic answer",
        "reasoning_content": "hidden synthetic trace",
        "tool_calls": [{"function": {"arguments": "hidden"}}],
    }}]}
    result = await callback.async_post_call_success_hook({}, None, response)
    assert result["choices"][0]["message"] == {
        "role": "assistant", "content": "Safe synthetic answer"
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [{"choices": []}, {"choices": [{"message": {"content": None}}]}])
async def test_post_call_rejects_unsupported_model_response(response):
    callback = OCIInstancePrincipalCallback()
    with pytest.raises(HTTPException) as error:
        await callback.async_post_call_success_hook({}, None, response)
    assert error.value.status_code == 502
