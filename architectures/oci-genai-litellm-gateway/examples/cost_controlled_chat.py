#!/usr/bin/env python3
"""A small client-side tier and usage policy for the reference gateway.

This is intentionally local and simple. It stores aggregate counters only;
prompts, responses, identifiers, and the gateway key are never written to disk.
Use LiteLLM Gateway with a database for shared, authoritative dollar budgets.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import urllib.error
import urllib.request

MAX_PROMPT_CHARS = 50_000
REASONING_SIGNALS = (
    "analyze", "compare", "evaluate", "root cause", "trade-off", "tradeoff",
    "multi-step", "reason through", "design a plan", "pros and cons",
)


def choose_route(prompt: str, mode: str) -> tuple[str, str]:
    """Return a model alias and a human-readable, non-sensitive reason."""
    if mode in ("basic", "reasoning"):
        return mode, "selected explicitly"
    lowered = prompt.casefold()
    if len(prompt) > 1_200:
        return "reasoning", "prompt exceeds the local complexity threshold"
    if any(signal in lowered for signal in REASONING_SIGNALS):
        return "reasoning", "prompt matched a local reasoning signal"
    return "basic", "default low-cost route"


def today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def load_ledger(path: Path, date: str) -> dict[str, int | str]:
    empty = {
        "date": date,
        "basic_calls": 0,
        "reasoning_calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read aggregate usage ledger: {error}") from error
    if not isinstance(data, dict) or data.get("date") != date:
        return empty
    for key in ("basic_calls", "reasoning_calls", "prompt_tokens", "completion_tokens"):
        if type(data.get(key)) is not int or data[key] < 0:
            raise RuntimeError("Aggregate usage ledger has an invalid format")
    return data


def save_ledger(path: Path, ledger: dict[str, int | str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(ledger, output, sort_keys=True)
            output.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def call_gateway(base_url: str, api_key: str, payload: dict) -> dict:
    request = urllib.request.Request(
        base_url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        # Do not print the provider body: future gateway versions might include
        # request data in errors. The reference currently returns fixed errors.
        raise RuntimeError(f"Gateway returned HTTP {error.code}") from None
    if not isinstance(result, dict):
        raise RuntimeError("Gateway returned an invalid response")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("auto", "basic", "reasoning"), default="auto")
    parser.add_argument("--max-reasoning-calls-per-day", type=int, default=5)
    parser.add_argument("--max-output-tokens", type=int)
    parser.add_argument(
        "--ledger", type=Path,
        default=Path(os.environ.get("LITELLM_EXAMPLE_LEDGER", ".litellm-example-usage.json")),
        help="Aggregate-only local usage file",
    )
    args = parser.parse_args()
    if args.max_reasoning_calls_per_day < 0:
        parser.error("--max-reasoning-calls-per-day must be zero or greater")

    prompt = sys.stdin.read().strip()
    if not prompt:
        parser.error("Provide a prompt on standard input")
    if len(prompt) > MAX_PROMPT_CHARS:
        parser.error(f"Prompt exceeds {MAX_PROMPT_CHARS:,} characters")

    route, reason = choose_route(prompt, args.mode)
    ledger = load_ledger(args.ledger, today_utc())
    if route == "reasoning" and ledger["reasoning_calls"] >= args.max_reasoning_calls_per_day:
        raise SystemExit("Reasoning-call limit reached; use --mode basic or wait for the UTC-day reset")

    max_tokens = args.max_output_tokens or (256 if route == "basic" else 1_024)
    if not 1 <= max_tokens <= 8_192:
        parser.error("--max-output-tokens must be between 1 and 8192")

    api_key = os.environ.get("GATEWAY_API_KEY", "")
    if not api_key:
        parser.error("Set GATEWAY_API_KEY")
    base_url = os.environ.get("GATEWAY_URL", "http://127.0.0.1:4000")
    print(f"Route: {route} ({reason}); output cap: {max_tokens} tokens", file=sys.stderr)
    result = call_gateway(base_url, api_key, {
        "model": route,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    })

    content = result["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise RuntimeError("Gateway returned an invalid message")
    print(content)

    ledger[route + "_calls"] += 1
    usage = result.get("usage")
    if isinstance(usage, dict):
        for key in ("prompt_tokens", "completion_tokens"):
            value = usage.get(key)
            if type(value) is int and value >= 0:
                ledger[key] += value
    save_ledger(args.ledger, ledger)
    print(
        f"UTC daily counters: basic={ledger['basic_calls']}, "
        f"reasoning={ledger['reasoning_calls']}; token usage is informational",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
