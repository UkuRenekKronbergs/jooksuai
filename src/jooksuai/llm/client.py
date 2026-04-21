"""LLM client with Anthropic + OpenAI backends.

Both backends return a `LLMRecommendation` built from the same JSON schema
(prompts.RESPONSE_SCHEMA). If the first JSON parse fails, we retry once with
an explicit reminder — per the project plan's risk B1/B2 on consistency.

Model versions are pinned in `config.py` rather than floating on `*-latest` —
risk 3 in the plan.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..config import OPENROUTER_BASE_URL, Config
from .prompts import PromptBundle


class LLMNotAvailable(RuntimeError):
    """Raised when no LLM provider credentials are configured."""


class LLMParseError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMRecommendation:
    category: str
    rationale: str
    modification: str | None
    confidence: str
    acknowledges_safety_flags: list[str]
    raw_text: str
    model: str
    prompt_version: str
    input_tokens: int | None = None
    output_tokens: int | None = None


def generate_recommendation(prompt: PromptBundle, config: Config) -> LLMRecommendation:
    if not config.has_llm:
        raise LLMNotAvailable(
            f"No credentials for provider '{config.llm_provider}'. "
            "Set ANTHROPIC_API_KEY / OPENAI_API_KEY / OPENROUTER_API_KEY in .env, "
            "or switch the UI to metrics-only mode."
        )

    if config.llm_provider == "anthropic":
        return _generate_anthropic(prompt, config)
    if config.llm_provider == "openai":
        return _generate_openai(prompt, config)
    if config.llm_provider == "openrouter":
        return _generate_openrouter(prompt, config)
    raise LLMNotAvailable(f"Unknown provider: {config.llm_provider}")


def _generate_anthropic(prompt: PromptBundle, config: Config) -> LLMRecommendation:
    try:
        import anthropic
    except ImportError as exc:
        raise LLMNotAvailable("anthropic SDK not installed — run `pip install anthropic`") from exc

    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    # Cache the system prompt — stable across calls, so saves tokens on repeat queries.
    response = client.messages.create(
        model=config.llm_model,
        max_tokens=1024,
        temperature=config.llm_temperature,
        system=[
            {
                "type": "text",
                "text": prompt.system,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": prompt.user}],
    )
    text = response.content[0].text if response.content else ""
    parsed = _parse_json_with_retry(text, client, prompt, config, _generate_anthropic_retry)
    usage = getattr(response, "usage", None)
    return _build_recommendation(
        parsed,
        raw_text=text,
        model=config.llm_model,
        prompt_version=prompt.version,
        input_tokens=getattr(usage, "input_tokens", None) if usage else None,
        output_tokens=getattr(usage, "output_tokens", None) if usage else None,
    )


def _generate_anthropic_retry(prompt: PromptBundle, config: Config, error: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    response = client.messages.create(
        model=config.llm_model,
        max_tokens=1024,
        temperature=config.llm_temperature,
        system=prompt.system,
        messages=[
            {"role": "user", "content": prompt.user},
            {"role": "assistant", "content": "Vabandust, annan kehtiva JSON-i."},
            {
                "role": "user",
                "content": (
                    f"Eelmine väljund ei parsitud ({error}). "
                    "Palun anna AINULT kehtiv JSON, mis vastab skeemile."
                ),
            },
        ],
    )
    return response.content[0].text if response.content else ""


def _generate_openai(prompt: PromptBundle, config: Config) -> LLMRecommendation:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise LLMNotAvailable("openai SDK not installed — run `pip install openai`") from exc

    client = OpenAI(api_key=config.openai_api_key)
    response = client.chat.completions.create(
        model=config.llm_model,
        temperature=config.llm_temperature,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": prompt.system},
            {"role": "user", "content": prompt.user},
        ],
    )
    text = response.choices[0].message.content or ""
    parsed = _parse_json_with_retry(text, client, prompt, config, _generate_openai_retry)
    usage = getattr(response, "usage", None)
    return _build_recommendation(
        parsed,
        raw_text=text,
        model=config.llm_model,
        prompt_version=prompt.version,
        input_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
        output_tokens=getattr(usage, "completion_tokens", None) if usage else None,
    )


def _generate_openrouter(prompt: PromptBundle, config: Config) -> LLMRecommendation:
    """OpenRouter via the OpenAI SDK (OpenRouter is API-compatible).

    Handy because one key gives access to Claude, GPT, Llama, Gemini etc — the
    model name is the `provider/model` slug on https://openrouter.ai/models.
    """
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise LLMNotAvailable("openai SDK not installed — run `pip install openai`") from exc

    client = OpenAI(
        api_key=config.openrouter_api_key,
        base_url=OPENROUTER_BASE_URL,
        default_headers={
            "HTTP-Referer": "https://github.com/UkuRenekKronbergs/jooksuai",
            "X-Title": "jooksuai",
        },
    )
    response = client.chat.completions.create(
        model=config.llm_model,
        temperature=config.llm_temperature,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": prompt.system},
            {"role": "user", "content": prompt.user},
        ],
    )
    text = response.choices[0].message.content or ""

    def _retry(prompt: PromptBundle, config: Config, error: str) -> str:
        retry = client.chat.completions.create(
            model=config.llm_model,
            temperature=config.llm_temperature,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
                {"role": "assistant", "content": "Vabandust, annan kehtiva JSON-i."},
                {
                    "role": "user",
                    "content": (
                        f"Eelmine väljund ei parsitud ({error}). "
                        "Palun anna AINULT kehtiv JSON, mis vastab skeemile."
                    ),
                },
            ],
        )
        return retry.choices[0].message.content or ""

    parsed = _parse_json_with_retry(text, client, prompt, config, _retry)
    usage = getattr(response, "usage", None)
    return _build_recommendation(
        parsed,
        raw_text=text,
        model=config.llm_model,
        prompt_version=prompt.version,
        input_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
        output_tokens=getattr(usage, "completion_tokens", None) if usage else None,
    )


def _generate_openai_retry(prompt: PromptBundle, config: Config, error: str) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=config.openai_api_key)
    response = client.chat.completions.create(
        model=config.llm_model,
        temperature=config.llm_temperature,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": prompt.system},
            {"role": "user", "content": prompt.user},
            {"role": "assistant", "content": "Vabandust, annan kehtiva JSON-i."},
            {
                "role": "user",
                "content": (
                    f"Eelmine väljund ei parsitud ({error}). "
                    "Palun anna AINULT kehtiv JSON, mis vastab skeemile."
                ),
            },
        ],
    )
    return response.choices[0].message.content or ""


def _parse_json_with_retry(text: str, _client, prompt: PromptBundle, config: Config, retry_fn) -> dict:
    try:
        return _extract_json(text)
    except LLMParseError as exc:
        retry_text = retry_fn(prompt, config, str(exc))
        return _extract_json(retry_text)


def _extract_json(text: str) -> dict:
    # Strip markdown fences if the model wrapped output in ```json ... ```
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        # Last-ditch: grab the first {...} block.
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        raise LLMParseError(f"invalid JSON: {exc.msg}") from exc


def _build_recommendation(
    parsed: dict,
    *,
    raw_text: str,
    model: str,
    prompt_version: str,
    input_tokens: int | None,
    output_tokens: int | None,
) -> LLMRecommendation:
    return LLMRecommendation(
        category=str(parsed.get("category", "")).strip(),
        rationale=str(parsed.get("rationale", "")).strip(),
        modification=(parsed.get("modification") or None),
        confidence=str(parsed.get("confidence", "keskmine")).strip(),
        acknowledges_safety_flags=list(parsed.get("acknowledges_safety_flags") or []),
        raw_text=raw_text,
        model=model,
        prompt_version=prompt_version,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
