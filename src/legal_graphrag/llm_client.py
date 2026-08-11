"""Shared, low-level LLM call helpers used across the whole pipeline."""

from __future__ import annotations

import json
import os
import re
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

MODEL_NAME = "gemini-flash-lite-latest"

API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise RuntimeError("GEMINI_API_KEY environment variable is not set.")

client = genai.Client(api_key=API_KEY)

# Gemma occasionally returns an empty response body with no error, so a
# couple of retries with backoff is enough to smooth over most of these.
_MAX_RETRIES = 2
_RETRY_DELAY_SECONDS = 2


def _clean_json(text: str) -> str:
    """Remove Markdown code fences from JSON output."""
    text = text.strip()

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    return text.strip()


def call_json(system: str, user: str, max_tokens: int = 2000):
    """Call the model expecting JSON output. Returns a dict or list."""

    last_error = None
    for attempt in range(_MAX_RETRIES + 1):
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                max_output_tokens=max_tokens,
            ),
        )

        raw = response.text or ""
        cleaned = _clean_json(raw)

        if not cleaned:
            last_error = ValueError("Model returned an empty response.")
            time.sleep(_RETRY_DELAY_SECONDS)
            continue

        try:
            return json.loads(cleaned)

        except json.JSONDecodeError:
            # Try extracting the first JSON object or array
            match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", cleaned)

            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    pass

            last_error = ValueError(f"Model did not return valid JSON:\n{raw}")
            time.sleep(_RETRY_DELAY_SECONDS)

    raise last_error


def call_text(system: str, user: str, max_tokens: int = 1500) -> str:
    """Call the model expecting plain text."""

    for attempt in range(_MAX_RETRIES + 1):
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system or None,
                max_output_tokens=max_tokens,
            ),
        )

        text = (response.text or "").strip()
        if text:
            return text

        if attempt < _MAX_RETRIES:
            time.sleep(_RETRY_DELAY_SECONDS)

    return ""
