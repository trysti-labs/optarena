"""
optarena/drivers/tool_chat.py
──────────────────────────
Raw tool-calling baseline drivers (no file edits) for tool-use cases - the
tool-domain counterpart to openai_chat.py's OpenAIChatDriver/OllamaChatDriver.
The model is given a tool schema and a goal; the driver runs the real
request -> tool_call -> execute-against-mock-service -> feed-result-back loop
until the model stops calling tools (or a turn limit is hit), then hands the
mock service's call log/final state to the tool-use oracle.

Two variants share one loop (ToolChatDriver.run_case), differing only in
which wire protocol carries the tool-calling turns:
  ToolChatDriver       → POST {base}/v1/chat/completions   (OpenAI-style tool_calls)
  OllamaToolChatDriver → POST {base}/api/chat               (Ollama-native tool_calls)
Same split, same reasoning, as openai_chat.py's two baseline drivers.
"""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path

from .._cases._mock_service import get_mock_service, get_tool_schemas
from .._cases._tool_evaluate import evaluate_tool_case
from ..scenario import Scenario
from .base import CaseResult, Driver
from .openai_chat import _post_json

_SYSTEM = (
    "You are an assistant that completes tasks by calling the tools "
    "provided to you. Call a tool whenever it makes progress on the task. "
    "When the task is fully complete, reply with a short plain-text "
    "confirmation and do not call any more tools."
)

# A case can override via "max_tool_turns"; this is the default and the
# schema-enforced ceiling alike keep a hung/looping model from consuming a
# case's whole timeout budget one tiny turn at a time.
_DEFAULT_MAX_TOOL_TURNS = 6


class ToolChatDriver(Driver):
    """OpenAI-protocol tool-calling loop. Shared by both variants below -
    only ``_chat`` (the wire format) differs; the loop, deadline handling,
    and oracle hookup are identical regardless of backend protocol."""

    name = "openai-tools"
    parallel_safe = True   # stateless HTTP per case, fresh service per case

    def _chat(self, messages: list[dict], schemas: list[dict], backend,
              timeout: float) -> tuple[dict, list[dict], dict]:
        """One request/response turn. Returns ``(raw_assistant_message,
        normalized_tool_calls, usage)`` - the raw message is appended to
        ``messages`` verbatim (so the next turn's request stays protocol-
        valid); the normalized list (``[{id, name, arguments}, ...]``) is
        what the driver loop actually dispatches against the mock service.
        """
        payload = {
            "model": backend.model,
            "messages": messages,
            "tools": schemas,
            "stream": False,
        }
        if backend.temperature is not None:
            payload["temperature"] = backend.temperature
        if backend.top_p is not None:
            payload["top_p"] = backend.top_p
        if backend.seed is not None:
            payload["seed"] = backend.seed
        body = _post_json(
            f"{backend.openai_base}/chat/completions",
            payload,
            timeout,
            headers={"Authorization": f"Bearer {backend.api_key}"},
        )
        message = body["choices"][0]["message"]
        tool_calls = _normalize_openai_tool_calls(message.get("tool_calls") or [])
        usage = body.get("usage") or {}
        return message, tool_calls, usage

    def run_case(self, case: dict, scenario: Scenario, workspace: Path) -> CaseResult:
        result = CaseResult(name=case["name"])
        timeout = scenario.timeout or case.get("timeout", 120)
        max_turns = case.get("max_tool_turns", _DEFAULT_MAX_TOOL_TURNS)

        service_name = case.get("tool_service")
        if not service_name:
            result.error = "case has no 'tool_service' - not a tool-use case"
            return result
        # A bad tool_service/tools name is a case-authoring mistake, not an
        # infrastructure failure - report it as a normal CaseResult.error
        # (base.py's contract: run_case must never raise for tool-level
        # failure), not an uncaught KeyError three frames up in the runner.
        try:
            service_cls = get_mock_service(service_name)
            schemas = get_tool_schemas(service_name, case.get("tools"))
        except KeyError as exc:
            result.error = str(exc)
            return result
        service = service_cls()
        service.seed(case.get("tool_service_seed") or {})

        messages: list[dict] = [{"role": "system", "content": _SYSTEM}]
        t0 = time.monotonic()
        n_prompts = len(case.get("prompts", []))
        deadline = t0 + timeout
        hit_turn_limit = False
        try:
            for i, prompt in enumerate(case.get("prompts", []), 1):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    result.error = f"case deadline ({timeout}s) exceeded before prompt {i}/{n_prompts}"
                    break
                messages.append({"role": "user", "content": prompt})
                for _turn in range(max_turns):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        result.error = f"case deadline ({timeout}s) exceeded mid tool-call loop"
                        break
                    raw_message, tool_calls, usage = self._chat(
                        messages, schemas, scenario.backend, remaining)
                    for key in ("prompt_tokens", "completion_tokens"):
                        if key in usage:
                            result.extra[key] = result.extra.get(key, 0) + usage[key]
                    messages.append(raw_message)
                    if not tool_calls:
                        break   # model is done for this prompt
                    for tc in tool_calls:
                        call_result = service.dispatch(tc["name"], tc["arguments"])
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": json.dumps(call_result, default=str),
                        })
                else:
                    hit_turn_limit = True
                if result.error:
                    break
        except (TimeoutError, socket.timeout):
            result.error = f"tool-calling loop timed out after {timeout}s"
        except Exception as exc:  # noqa: BLE001 - report, don't crash the run
            result.error = f"{type(exc).__name__}: {exc}"
        result.duration_s = time.monotonic() - t0

        result.extra["tool_calls"] = service.call_log
        if hit_turn_limit:
            result.extra["hit_turn_limit"] = True
        result.failures, result.extra["oracle"] = evaluate_tool_case(case, service)
        result.passed = result.error is None and not result.failures
        return result


class OllamaToolChatDriver(ToolChatDriver):
    """Same loop, Ollama-native ``/api/chat`` wire format - see
    openai_chat.OllamaChatDriver for why this is a thin protocol override
    rather than a separate implementation."""

    name = "ollama-tools"

    def _chat(self, messages: list[dict], schemas: list[dict], backend,
              timeout: float) -> tuple[dict, list[dict], dict]:
        payload = {
            "model": backend.model,
            "messages": messages,
            "tools": schemas,
            "stream": False,
        }
        options = {}
        if backend.num_ctx:
            options["num_ctx"] = backend.num_ctx
        if backend.temperature is not None:
            options["temperature"] = backend.temperature
        if backend.top_p is not None:
            options["top_p"] = backend.top_p
        if backend.seed is not None:
            options["seed"] = backend.seed
        if options:
            payload["options"] = options
        body = _post_json(
            f"{backend.base_url.rstrip('/')}/api/chat",
            payload,
            timeout,
        )
        message = body.get("message", {}) or {}
        tool_calls = _normalize_ollama_tool_calls(message.get("tool_calls") or [])
        usage = {
            "prompt_tokens": body.get("prompt_eval_count", 0),
            "completion_tokens": body.get("eval_count", 0),
        }
        return message, tool_calls, usage


def _normalize_openai_tool_calls(raw_tool_calls: list[dict]) -> list[dict]:
    """OpenAI's protocol sends ``arguments`` as a JSON-encoded STRING inside
    ``function.arguments`` - decode it here so the driver loop and the
    oracle both always see a plain dict, regardless of protocol."""
    out = []
    for i, tc in enumerate(raw_tool_calls):
        fn = tc.get("function", {}) or {}
        args_raw = fn.get("arguments", "{}")
        if isinstance(args_raw, str):
            try:
                args = json.loads(args_raw) if args_raw.strip() else {}
            except json.JSONDecodeError:
                args = {}
        else:
            args = args_raw or {}
        out.append({"id": tc.get("id") or f"call_{i}", "name": fn.get("name", ""), "arguments": args})
    return out


def _normalize_ollama_tool_calls(raw_tool_calls: list[dict]) -> list[dict]:
    """Ollama's native protocol already sends ``arguments`` as a real JSON
    object (not a string) and has no per-call ``id`` - synthesize one so the
    dispatch loop's shape matches the OpenAI-protocol path exactly."""
    out = []
    for i, tc in enumerate(raw_tool_calls):
        fn = tc.get("function", {}) or {}
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except json.JSONDecodeError:
                args = {}
        out.append({"id": tc.get("id") or f"call_{i}", "name": fn.get("name", ""), "arguments": args})
    return out
