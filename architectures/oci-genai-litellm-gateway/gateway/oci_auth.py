"""LiteLLM Gateway callback for a small, text-only OCI reference surface."""

from __future__ import annotations

import asyncio
from typing import Final

from fastapi import HTTPException
from litellm.integrations.custom_logger import CustomLogger

ALLOWED_MODELS: Final = frozenset({"basic", "reasoning"})
ALLOWED_ROLES: Final = frozenset({"system", "user", "assistant"})
MAX_MESSAGES: Final = 100
MAX_TEXT_CHARS: Final = 50_000
MAX_OUTPUT_TOKENS: Final = {"basic": 512, "reasoning": 4096}
UNSUPPORTED_FIELDS: Final = frozenset(
    {
        "api_base",
        "api_key",
        "audio",
        "base_url",
        "content_safety",
        "custom_llm_provider",
        "extra_headers",
        "function_call",
        "functions",
        "guardrails",
        "headers",
        "max_completion_tokens",
        "modalities",
        "mock_response",
        "organization",
        "reasoning_effort",
        "response_format",
        "tool_choice",
        "tools",
        "user",
    }
)


class RefreshingOCISigner:
    """Use the OCI SDK interface that refreshes instance-principal tokens."""

    def __init__(self, signer):
        self._signer = signer

    def do_request_sign(self, request, enforce_content_headers=True):
        # LiteLLM 1.103 calls do_request_sign directly. The OCI SDK refreshes
        # the security token in the signer's public callable instead.
        return self._signer(request, enforce_content_headers=enforce_content_headers)

    def __deepcopy__(self, memo):
        # LiteLLM snapshots request parameters. Keep the SDK signer's lock and
        # renewable identity in one process-local object.
        return self


def _invalid(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


class OCIInstancePrincipalCallback(CustomLogger):
    def __init__(self) -> None:
        self._signer = None
        self._signer_lock = asyncio.Lock()

    async def _get_signer(self):
        if self._signer is not None:
            return self._signer
        async with self._signer_lock:
            if self._signer is None:
                try:
                    from oci.auth.signers import InstancePrincipalsSecurityTokenSigner

                    signer = await asyncio.to_thread(InstancePrincipalsSecurityTokenSigner)
                    self._signer = RefreshingOCISigner(signer)
                except Exception:
                    raise HTTPException(
                        status_code=503, detail="OCI instance principal unavailable"
                    ) from None
        return self._signer

    @staticmethod
    def _validate_and_bound(data: dict) -> None:
        model = data.get("model")
        if model not in ALLOWED_MODELS:
            raise _invalid("Use the basic or reasoning model alias")

        if data.get("stream", False) is not False:
            raise _invalid("Streaming is not enabled in this reference")
        if data.get("n", 1) != 1:
            raise _invalid("Only one completion is supported")
        if any(field in data and data[field] is not None for field in UNSUPPORTED_FIELDS):
            raise _invalid("This reference supports text chat fields only")

        messages = data.get("messages")
        if not isinstance(messages, list) or not 1 <= len(messages) <= MAX_MESSAGES:
            raise _invalid("Provide between 1 and 100 text messages")
        total_chars = 0
        for message in messages:
            if not isinstance(message, dict) or set(message) != {"role", "content"}:
                raise _invalid("Messages support role and string content only")
            if message.get("role") not in ALLOWED_ROLES:
                raise _invalid("Unsupported message role")
            content = message.get("content")
            if not isinstance(content, str) or not content:
                raise _invalid("Message content must be a non-empty string")
            total_chars += len(content)
        if total_chars > MAX_TEXT_CHARS:
            raise _invalid("Message text exceeds the 50000 character limit")

        limit = MAX_OUTPUT_TOKENS[model]
        requested = data.get("max_tokens", limit)
        if type(requested) is not int or not 1 <= requested <= limit:
            raise _invalid(f"max_tokens must be between 1 and {limit} for {model}")
        data["max_tokens"] = requested
        data["stream"] = False

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if call_type not in ("completion", "acompletion"):
            raise _invalid("Only chat completions are enabled")
        self._validate_and_bound(data)
        data["oci_signer"] = await self._get_signer()
        return data

    async def async_post_call_success_hook(self, data, user_api_key_dict, response):
        """Keep only the scanned answer surface after post-call guardrails run."""
        choices = response.get("choices") if hasattr(response, "get") else getattr(response, "choices", None)
        if not isinstance(choices, list) or len(choices) != 1:
            raise HTTPException(status_code=502, detail="Unsupported model response")
        choice = choices[0]
        message = choice.get("message") if hasattr(choice, "get") else getattr(choice, "message", None)
        if message is None:
            raise HTTPException(status_code=502, detail="Unsupported model response")
        content = message.get("content") if hasattr(message, "get") else getattr(message, "content", None)
        if not isinstance(content, str) or len(content) > MAX_TEXT_CHARS:
            raise HTTPException(status_code=502, detail="Unsupported model response")
        for field in ("reasoning", "reasoning_content", "tool_calls", "function_call", "audio"):
            if hasattr(message, "pop"):
                message.pop(field, None)
            elif hasattr(message, field):
                setattr(message, field, None)
        return response


oci_instance_principal: Final = OCIInstancePrincipalCallback()
