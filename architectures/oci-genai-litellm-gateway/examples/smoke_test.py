#!/usr/bin/env python3
"""Only synthetic data. Standard library client: no installation required."""
import json
import os
import urllib.error
import urllib.request

BASE = os.environ.get("GATEWAY_URL", "http://127.0.0.1:4000").rstrip("/")


def call(path, payload=None):
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": "Bearer " + os.environ["GATEWAY_API_KEY"], "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        try:
            detail = json.load(error)
        except (json.JSONDecodeError, UnicodeDecodeError):
            detail = {"error": "Gateway returned a non-JSON error"}
        raise RuntimeError(f"Gateway HTTP {error.code}: {detail}") from None


def main():
    print("Health:", call("/health/liveliness"))
    print("Routes:", [m["id"] for m in call("/v1/models")["data"]])
    synthetic = "Contact jane.test@example.com. MRN: ABC123456. DOB: 01/02/1980."
    for model in ("basic", "reasoning"):
        response = call("/v1/chat/completions", {
            "model": model,
            "messages": [{"role": "user", "content":
                "This is synthetic test data: " + synthetic +
                " First repeat every identifier exactly, then explain in one sentence why identifiers should be removed before analytics."}],
            "max_tokens": 512 if model == "basic" else 1024,
        })
        content = response["choices"][0]["message"]["content"]
        message = response["choices"][0]["message"]
        assert content, "Model returned empty content"
        for value in ("jane.test@example.com", "ABC123456", "01/02/1980"):
            assert value not in content, "Synthetic identifier reached the response"
        for field in ("reasoning", "reasoning_content", "tool_calls", "function_call", "audio"):
            assert not message.get(field), f"Unexpected response field: {field}"
        print(model + ":", content)
        print("Usage:", response.get("usage", {}))
    print("PASS: both OCI routes and synthetic PHI redaction")


if __name__ == "__main__":
    main()
