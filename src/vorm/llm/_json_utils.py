"""Tolerant JSON extraction shared between the daily advisor and plan generator.

Open models (Gemma, Llama) routinely wrap JSON in ```json fences or add prose.
Both LLM call sites need the same forgiveness, so the parsing lives here.
"""

from __future__ import annotations

import json
import re


def extract_json_object(text: str) -> dict:
    """Parse a dict from possibly-noisy LLM output.

    Strategy: strip Markdown fences, try `json.loads`, fall back to grabbing
    the first `{...}` block. Raises ``ValueError`` on failure — callers wrap
    it in their own exception type as needed.
    """
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        raise ValueError(f"invalid JSON: {exc.msg}") from exc
