"""LLM assistant proxy for Optimal Plan page (OpenAI / Anthropic / OpenClaw)."""
from __future__ import annotations

import json
import os
from typing import Any

import httpx
from pydantic import BaseModel, Field

DEFAULT_OPENAI_MODEL = os.getenv("ASSISTANT_OPENAI_MODEL", "gpt-4o-mini")
DEFAULT_ANTHROPIC_MODEL = os.getenv("ASSISTANT_ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
DEFAULT_OPENCLAW_MODEL = os.getenv("OPENCLAW_MODEL", "openclaw/default")


class AssistantChatRequest(BaseModel):
    messages: list[dict[str, Any]]
    page_context: dict[str, Any] = Field(default_factory=dict)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    provider: str | None = None


class AssistantChatResponse(BaseModel):
    message: dict[str, Any]
    provider: str
    model: str


class AssistantProvidersResponse(BaseModel):
    providers: list[str]
    default: str


def list_assistant_providers() -> AssistantProvidersResponse:
    providers: list[str] = []
    if os.getenv("OPENAI_API_KEY"):
        providers.append("openai")
    if os.getenv("ANTHROPIC_API_KEY"):
        providers.append("anthropic")
    if _openclaw_configured():
        providers.append("openclaw")

    env_default = (os.getenv("ASSISTANT_PROVIDER") or "openai").strip().lower()
    if env_default in providers:
        default = env_default
    elif providers:
        default = providers[0]
    else:
        default = "openai"

    return AssistantProvidersResponse(providers=providers, default=default)


def _openclaw_configured() -> bool:
    return bool(
        os.getenv("OPENCLAW_GATEWAY_URL")
        and (os.getenv("OPENCLAW_GATEWAY_TOKEN") or os.getenv("OPENCLAW_API_KEY"))
    )


def _openclaw_gateway_url() -> str:
    return (os.getenv("OPENCLAW_GATEWAY_URL") or "http://127.0.0.1:18789").rstrip("/")


def _openclaw_token() -> str:
    token = os.getenv("OPENCLAW_GATEWAY_TOKEN") or os.getenv("OPENCLAW_API_KEY") or ""
    if not token:
        raise RuntimeError("OPENCLAW_GATEWAY_TOKEN vantar")
    return token


def _build_system_prompt(page_context: dict[str, Any]) -> str:
    ctx = json.dumps(page_context, ensure_ascii=False, indent=2)
    return f"""Þú ert aðstoðarmaður á Optimal Plan síðu í Nostradamus innkaupakerfinu.
Notandinn sér birgðaþróunargröf og vörulista með síum, WHERE-skilyrðum og hermingu (sim_prep) per vöru.

Þú getur:
- Svarað spurningum um gögn, dálka og birgðaþróun
- Beðið um að setja WHERE-síu, textaleit eða hreinsa síur (tools)
- Opnað detail panel fyrir vöru eftir PN eða id
- Stillt hermingarbreytur (þjónustustig, dagar, fjöldi hermana)
- Gefði samantekt á síuðum vörum
- Búið til PDF skýrslu af síuðum vörum (export_purchase_plan_pdf) — tafla + birgðaþróunar-línurit ef simulation gögn liggja fyrir

Notaðu tools þegar notandi biður um aðgerð — ekki bara lýsa hvernig á að gera það.
Fyrir PDF: notaðu export_purchase_plan_pdf á síuðum vörum (sjálfgefið aðeins með innkaupatillögu > 0, með birgðaþróunar-grafi).
WHERE tjáning notar SQL-líkt mál eins og í gridinu (t.d. stock_level > 0 AND vendor_name LIKE '%Acme%').

Núverandi síðuástand (JSON):
{ctx}
"""


def _resolve_provider(requested: str | None) -> str:
    available = list_assistant_providers()
    pref = (requested or os.getenv("ASSISTANT_PROVIDER") or available.default).strip().lower()

    if pref in available.providers:
        return pref

    if available.providers:
        labels = {
            "openai": "OpenAI (OPENAI_API_KEY vantar á db-api)",
            "anthropic": "Claude (ANTHROPIC_API_KEY vantar á db-api)",
            "openclaw": "OpenClaw (gateway vantar á db-api)",
        }
        raise RuntimeError(
            f"{labels.get(pref, pref)} — tiltækir: {', '.join(available.providers)}",
        )

    raise RuntimeError(
        "Enginn LLM stilltur (OPENAI_API_KEY, ANTHROPIC_API_KEY eða OpenClaw gateway)",
    )


def _parse_openai_compatible_message(data: dict[str, Any]) -> dict[str, Any]:
    choice = data["choices"][0]["message"]
    out: dict[str, Any] = {"role": "assistant", "content": choice.get("content")}
    if choice.get("tool_calls"):
        out["tool_calls"] = choice["tool_calls"]
    return out


def _openai_compatible_chat(
    *,
    url: str,
    api_key: str,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    model: str,
    extra_headers: dict[str, str] | None = None,
    timeout: float = 120.0,
) -> tuple[dict[str, Any], str]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system}, *messages],
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, headers=headers, json=payload)
        if resp.status_code == 404 and "chat/completions" in url:
            raise RuntimeError(
                "OpenClaw /v1/chat/completions er ekki virkt — set gateway.http.endpoints.chatCompletions.enabled=true í openclaw.json og endurræstu gateway",
            )
        if resp.status_code == 429:
            try:
                err_body = resp.json().get("error", {})
            except Exception:
                err_body = {}
            code = err_body.get("code") or ""
            if code == "insufficient_quota":
                raise RuntimeError(
                    "OpenAI kredit klárað — bættu við credits á platform.openai.com eða veldu OpenClaw/Claude",
                )
            raise RuntimeError("OpenAI rate limit (429) — reyndu aftur eftir smá stund")
        if resp.status_code == 401:
            raise RuntimeError("OpenAI API lykill ógildur (401) — athugaðu OPENAI_API_KEY á db-api")
        resp.raise_for_status()
        data = resp.json()

    return _parse_openai_compatible_message(data), model


def _openai_chat(
    *,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    model: str,
) -> tuple[dict[str, Any], str]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY vantar")
    return _openai_compatible_chat(
        url="https://api.openai.com/v1/chat/completions",
        api_key=api_key,
        system=system,
        messages=messages,
        tools=tools,
        model=model,
    )


def _openclaw_chat(
    *,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    model: str,
) -> tuple[dict[str, Any], str]:
    extra_headers: dict[str, str] = {}
    backend_model = os.getenv("OPENCLAW_BACKEND_MODEL", "").strip()
    if backend_model:
        extra_headers["x-openclaw-model"] = backend_model

    return _openai_compatible_chat(
        url=f"{_openclaw_gateway_url()}/v1/chat/completions",
        api_key=_openclaw_token(),
        system=system,
        messages=messages,
        tools=tools,
        model=model,
        extra_headers=extra_headers,
        timeout=float(os.getenv("OPENCLAW_TIMEOUT_SECONDS", "180")),
    )


def _anthropic_chat(
    *,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    model: str,
) -> tuple[dict[str, Any], str]:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY vantar")

    anthropic_messages: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        if role == "tool":
            anthropic_messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id"),
                    "content": msg.get("content") or "",
                }],
            })
            continue
        if role == "assistant" and msg.get("tool_calls"):
            content_blocks: list[dict[str, Any]] = []
            if msg.get("content"):
                content_blocks.append({"type": "text", "text": msg["content"]})
            for tc in msg["tool_calls"]:
                fn = tc.get("function") or {}
                args_raw = fn.get("arguments") or "{}"
                try:
                    args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                except json.JSONDecodeError:
                    args = {}
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id"),
                    "name": fn.get("name"),
                    "input": args,
                })
            anthropic_messages.append({"role": "assistant", "content": content_blocks})
            continue
        anthropic_messages.append({
            "role": "user" if role == "user" else "assistant",
            "content": msg.get("content") or "",
        })

    anthropic_tools = []
    for tool in tools:
        fn = tool.get("function") or {}
        anthropic_tools.append({
            "name": fn.get("name"),
            "description": fn.get("description"),
            "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
        })

    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": 4096,
        "system": system,
        "messages": anthropic_messages,
    }
    if anthropic_tools:
        payload["tools"] = anthropic_tools

    with httpx.Client(timeout=120.0) as client:
        resp = client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    text_parts = []
    tool_calls = []
    for block in data.get("content") or []:
        if block.get("type") == "text":
            text_parts.append(block.get("text") or "")
        elif block.get("type") == "tool_use":
            tool_calls.append({
                "id": block.get("id"),
                "type": "function",
                "function": {
                    "name": block.get("name"),
                    "arguments": json.dumps(block.get("input") or {}),
                },
            })

    out: dict[str, Any] = {
        "role": "assistant",
        "content": "\n".join(text_parts).strip() or None,
    }
    if tool_calls:
        out["tool_calls"] = tool_calls
    return out, model


def run_assistant_chat(body: AssistantChatRequest) -> AssistantChatResponse:
    provider = _resolve_provider(body.provider)
    system = _build_system_prompt(body.page_context)

    if provider == "openclaw":
        model = DEFAULT_OPENCLAW_MODEL
        message, model = _openclaw_chat(
            system=system,
            messages=body.messages,
            tools=body.tools,
            model=model,
        )
    elif provider == "openai":
        model = DEFAULT_OPENAI_MODEL
        message, model = _openai_chat(
            system=system,
            messages=body.messages,
            tools=body.tools,
            model=model,
        )
    else:
        model = DEFAULT_ANTHROPIC_MODEL
        message, model = _anthropic_chat(
            system=system,
            messages=body.messages,
            tools=body.tools,
            model=model,
        )

    return AssistantChatResponse(message=message, provider=provider, model=model)
