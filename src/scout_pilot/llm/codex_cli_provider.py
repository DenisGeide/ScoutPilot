"""Codex CLI adapter behind the provider-neutral LLM interface."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

from scout_pilot.llm.errors import malformed_response, provider_error_result
from scout_pilot.llm.types import (
    LlmErrorCode,
    LlmFinishReason,
    LlmProviderRequest,
    LlmProviderResponse,
    LlmProviderResult,
    LlmToolCall,
)
from scout_pilot.tools.types import ToolSchema


CodexRunner = Callable[[str, str, float], Awaitable[tuple[int, str, str]]]

_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "content": {"type": "string"},
        "tool_name": {"type": "string"},
        "tool_arguments_json": {"type": "string"},
        "finish_reason": {"type": "string", "enum": ["stop", "tool_calls"]},
    },
    "required": ["content", "tool_name", "tool_arguments_json", "finish_reason"],
    "additionalProperties": False,
}


class CodexCliLlmProvider:
    """Use an authenticated local Codex CLI as a bounded LLM adapter."""

    def __init__(
        self,
        executable: str | None = None,
        runner: CodexRunner | None = None,
    ) -> None:
        self._executable = executable or _find_codex_executable()
        self._runner = runner or _run_codex

    async def complete(self, request: LlmProviderRequest) -> LlmProviderResult:
        if not self._executable:
            return provider_error_result(
                LlmErrorCode.CONFIGURATION_ERROR,
                "Codex CLI is not installed or is not available on PATH.",
                retryable=False,
            )

        timeout = request.timeout_seconds or 60.0
        prompt = _build_codex_prompt(request)
        try:
            return_code, output, stderr = await self._runner(
                self._executable,
                prompt,
                timeout,
            )
        except TimeoutError as exc:
            return provider_error_result(
                LlmErrorCode.TIMEOUT,
                str(exc) or "Codex CLI timed out.",
                retryable=True,
            )
        except Exception as exc:
            return provider_error_result(
                LlmErrorCode.PROVIDER_UNAVAILABLE,
                f"Codex CLI failed to start: {type(exc).__name__}.",
                retryable=True,
            )

        if return_code != 0:
            return _codex_process_error(stderr or output)
        return _parse_codex_output(output)


def _find_codex_executable() -> str | None:
    candidates = ("codex.cmd", "codex.exe", "codex") if os.name == "nt" else ("codex",)
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def _build_codex_prompt(request: LlmProviderRequest) -> str:
    messages = [
        {"role": message.role.value, "content": message.content}
        for message in request.messages
    ]
    tools = [_tool_payload(schema) for schema in request.tools]
    payload = {
        "messages": messages,
        "available_tools": tools,
        "max_output_tokens": request.max_output_tokens,
    }
    return (
        "Act only as the provider-neutral reasoning backend for Scout Pilot. "
        "Do not inspect files, run shell commands, browse independently, or execute tools. "
        "Read only the request payload below. Return one JSON object matching the supplied "
        "output schema. If one browser tool is needed, set finish_reason to tool_calls, use "
        "the exact tool name, and put a valid JSON object string in tool_arguments_json. "
        "Otherwise set finish_reason to stop, put the answer in content, leave tool_name empty, "
        "and set tool_arguments_json to '{}'. Never invent tools.\n\nREQUEST:\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )


def _tool_payload(schema: ToolSchema) -> dict[str, object]:
    return {
        "name": schema.name,
        "description": schema.description,
        "arguments": [
            {
                "name": field.name,
                "type": field.value_type.value,
                "description": field.description,
                "required": field.required,
                "enum": list(field.enum_values),
            }
            for field in schema.input_schema.fields
        ],
    }


async def _run_codex(
    executable: str,
    prompt: str,
    timeout_seconds: float,
) -> tuple[int, str, str]:
    with tempfile.TemporaryDirectory(prefix="scout-pilot-codex-") as raw_dir:
        workdir = Path(raw_dir)
        schema_path = workdir / "output-schema.json"
        output_path = workdir / "last-message.json"
        schema_path.write_text(
            json.dumps(_OUTPUT_SCHEMA, ensure_ascii=True),
            encoding="utf-8",
        )
        process = await asyncio.create_subprocess_exec(
            executable,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--ignore-user-config",
            "--ignore-rules",
            "--color",
            "never",
            "-c",
            'model_reasoning_effort="low"',
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
            "-",
            cwd=workdir,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(prompt.encode("utf-8")),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            raise TimeoutError("Codex CLI timed out before returning a response.") from None

        output = (
            output_path.read_text(encoding="utf-8")
            if output_path.exists()
            else stdout_bytes.decode("utf-8", errors="replace")
        )
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        return process.returncode or 0, output, stderr


def _parse_codex_output(raw_output: str) -> LlmProviderResult:
    try:
        payload = json.loads(raw_output.strip())
    except json.JSONDecodeError:
        return malformed_response("Codex CLI returned malformed structured output.")
    if not isinstance(payload, dict):
        return malformed_response("Codex CLI output was not a JSON object.")

    finish_reason = payload.get("finish_reason")
    content = str(payload.get("content") or "").strip()
    if finish_reason == "tool_calls":
        tool_name = str(payload.get("tool_name") or "").strip()
        if not tool_name:
            return malformed_response("Codex CLI selected an empty tool name.")
        try:
            arguments = json.loads(str(payload.get("tool_arguments_json") or "{}"))
        except json.JSONDecodeError:
            return malformed_response("Codex CLI returned malformed tool arguments.")
        if not isinstance(arguments, dict):
            return malformed_response("Codex CLI tool arguments were not an object.")
        return LlmProviderResult(
            success=True,
            response=LlmProviderResponse(
                tool_calls=(LlmToolCall(name=tool_name, arguments=arguments),),
                finish_reason=LlmFinishReason.TOOL_CALLS,
                raw_provider_name="codex_cli",
            ),
        )

    if finish_reason != "stop" or not content:
        return malformed_response("Codex CLI returned neither an answer nor a tool call.")
    return LlmProviderResult(
        success=True,
        response=LlmProviderResponse(
            content=content,
            finish_reason=LlmFinishReason.STOP,
            raw_provider_name="codex_cli",
        ),
    )


def _codex_process_error(message: str) -> LlmProviderResult:
    lowered = message.casefold()
    if "not logged in" in lowered or "login" in lowered and "required" in lowered:
        return provider_error_result(
            LlmErrorCode.INVALID_CREDENTIALS,
            "Codex CLI is not authenticated. Run `codex login`.",
            retryable=False,
        )
    if "rate limit" in lowered or "usage limit" in lowered or "quota" in lowered:
        return provider_error_result(
            LlmErrorCode.RATE_LIMIT,
            "Codex CLI usage limit was reached.",
            retryable=True,
        )
    return provider_error_result(
        LlmErrorCode.PROVIDER_UNAVAILABLE,
        "Codex CLI returned a non-zero exit code.",
        retryable=True,
    )
