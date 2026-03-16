"""
Grok (xAI) backend – the primary new backend for PV-sizing.

Uses the OpenAI SDK with ``base_url="https://api.x.ai/v1"`` for
chat completions, with optional structured-output (JSON schema)
support, retry logic, and a single repair attempt on validation failure.

Falls back to raw ``requests`` if the ``openai`` package is not
installed.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from typing import Any, Dict, List, Optional

from backends.base import BaseBackend
from schemas.pv_recommendation_schema import (
    PV_RECOMMENDATION_SCHEMA,
    PV_RECOMMENDATION_SCHEMA_JSON,
    build_repair_prompt,
    validate_recommendation,
)
from utils.json_extract import extract_json

logger = logging.getLogger(__name__)

# ── Retry settings ───────────────────────────────────────────
_MAX_RETRIES = 5
_BASE_BACKOFF_S = 3.0
_MAX_BACKOFF_S = 60.0
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _backoff(attempt: int) -> float:
    """Exponential backoff with jitter, capped at _MAX_BACKOFF_S."""
    delay = min(_BASE_BACKOFF_S * (2 ** attempt), _MAX_BACKOFF_S)
    return delay + random.uniform(0, delay * 0.25)


class GrokBackend(BaseBackend):
    """xAI / Grok inference backend.

    Parameters
    ----------
    api_key : str
        xAI API key (``XAI_API_KEY``).
    base_url : str
        Base URL for the xAI API.
    model : str
        Model identifier (e.g. ``grok-4-1-fast-non-reasoning``).
    timeout_s : float
        HTTP timeout in seconds.
    use_structured_output : bool
        If True, include the PV recommendation JSON schema in the
        request so the model produces structured output.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.x.ai/v1",
        model: str = "grok-4-1-fast-non-reasoning",
        timeout_s: float = 120.0,
        use_structured_output: bool = False,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.use_structured_output = use_structured_output

        # Try to use the OpenAI SDK; fall back to requests
        self._client = None
        self._use_sdk = False
        try:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout_s,
                max_retries=0,  # disable SDK's built-in retry; _call_with_retry handles it
            )
            self._use_sdk = True
            logger.info("GrokBackend: using OpenAI SDK (base_url=%s)", self.base_url)
        except ImportError:
            logger.info("GrokBackend: openai not installed – using raw requests")

    # ── Public interface ─────────────────────────────────────

    def generate(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> str:
        """Send *prompt* to xAI and return the assistant's response text.

        If structured output is enabled the response is validated against
        the PV recommendation schema.  On validation failure a single
        repair attempt is made.
        """
        messages = self._build_messages(prompt, system)

        logger.info(
            "GrokBackend.generate  model=%s  prompt_chars=%d  structured=%s",
            self.model,
            len(prompt),
            self.use_structured_output,
        )
        t0 = time.time()

        raw_text = self._call_with_retry(messages, max_tokens, temperature)

        latency = time.time() - t0
        logger.info("GrokBackend response  chars=%d  latency=%.1fs", len(raw_text), latency)

        if not self.use_structured_output:
            return raw_text

        # ── Validate structured output ───────────────────────
        parsed = extract_json(raw_text)
        if parsed is None:
            logger.warning("Could not parse JSON from model response – attempting repair")
            return self._repair(raw_text, ["Response is not valid JSON"], messages, max_tokens, temperature)

        is_valid, errors = validate_recommendation(parsed)
        if is_valid:
            logger.info("Schema validation passed on first try")
            return json.dumps(parsed, indent=2)

        logger.warning("Schema validation failed (%d errors) – attempting repair", len(errors))
        return self._repair(raw_text, errors, messages, max_tokens, temperature)

    def chat(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = 0.3,
    ) -> str:
        """Multi-turn chat without structured-output enforcement.

        Used for follow-up Q&A where the response is free-form text,
        not a JSON schema.
        """
        logger.info(
            "GrokBackend.chat  model=%s  messages=%d  total_chars=%d",
            self.model, len(messages),
            sum(len(m.get("content", "")) for m in messages),
        )
        t0 = time.time()

        raw_text = self._call_chat_with_retry(messages, max_tokens, temperature)

        latency = time.time() - t0
        logger.info("GrokBackend.chat response  chars=%d  latency=%.1fs", len(raw_text), latency)
        return raw_text

    def _call_chat_with_retry(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> str:
        """Call xAI for plain chat (no response_format) with retry."""
        last_exc: Optional[Exception] = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                if self._use_sdk:
                    return self._call_chat_sdk(messages, max_tokens, temperature)
                else:
                    return self._call_chat_requests(messages, max_tokens, temperature)
            except Exception as exc:
                last_exc = exc
                status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
                if status and int(status) == 401:
                    raise
                if attempt < _MAX_RETRIES:
                    wait = _backoff(attempt)
                    logger.warning(
                        "xAI chat call failed (attempt %d/%d): %s – retrying in %.1fs",
                        attempt + 1, _MAX_RETRIES + 1, exc, wait,
                    )
                    time.sleep(wait)
                else:
                    raise
        raise last_exc  # type: ignore[misc]

    def _call_chat_sdk(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> str:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        text = resp.choices[0].message.content or ""
        usage = getattr(resp, "usage", None)
        if usage:
            logger.info(
                "Chat token usage: prompt=%s completion=%s total=%s",
                getattr(usage, "prompt_tokens", "?"),
                getattr(usage, "completion_tokens", "?"),
                getattr(usage, "total_tokens", "?"),
            )
        return text

    def _call_chat_requests(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> str:
        import requests as _requests

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        resp = _requests.post(url, json=payload, headers=headers, timeout=self.timeout_s)
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage")
        if usage:
            logger.info(
                "Chat token usage: prompt=%s completion=%s total=%s",
                usage.get("prompt_tokens", "?"),
                usage.get("completion_tokens", "?"),
                usage.get("total_tokens", "?"),
            )
        return text

    # ── Internal helpers ─────────────────────────────────────

    def _build_messages(self, prompt: str, system: str) -> List[Dict[str, str]]:
        msgs: List[Dict[str, str]] = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})
        return msgs

    def _rebuild_client(self) -> None:
        """Recreate the OpenAI SDK client (clears stale connection pool)."""
        if not self._use_sdk:
            return
        try:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout_s,
                max_retries=0,
            )
            logger.info("Rebuilt OpenAI SDK client (fresh connection pool)")
        except Exception:
            pass

    def _call_with_retry(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> str:
        """Call xAI with exponential-backoff retry on transient errors."""
        last_exc: Optional[Exception] = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                if self._use_sdk:
                    return self._call_sdk(messages, max_tokens, temperature)
                else:
                    return self._call_requests(messages, max_tokens, temperature)
            except Exception as exc:
                last_exc = exc
                status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
                if status and int(status) == 401:
                    raise

                is_connection_error = "Connection" in type(exc).__name__ or "ReadError" in str(type(exc))
                if is_connection_error:
                    self._rebuild_client()

                if attempt < _MAX_RETRIES:
                    wait = _backoff(attempt)
                    logger.warning(
                        "xAI call failed (attempt %d/%d): %s – retrying in %.1fs",
                        attempt + 1,
                        _MAX_RETRIES + 1,
                        exc,
                        wait,
                    )
                    time.sleep(wait)
                else:
                    raise
        raise last_exc  # unreachable, but satisfies type checker

    # ── OpenAI SDK path ──────────────────────────────────────

    def _call_sdk(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> str:
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if self.use_structured_output:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "pv_recommendation",
                    "strict": True,
                    "schema": PV_RECOMMENDATION_SCHEMA,
                },
            }

        resp = self._client.chat.completions.create(**kwargs)
        text = resp.choices[0].message.content or ""

        # Log token usage if available
        usage = getattr(resp, "usage", None)
        if usage:
            logger.info(
                "Token usage: prompt=%s completion=%s total=%s",
                getattr(usage, "prompt_tokens", "?"),
                getattr(usage, "completion_tokens", "?"),
                getattr(usage, "total_tokens", "?"),
            )
        return text

    # ── Raw requests fallback ────────────────────────────────

    def _call_requests(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> str:
        import requests as _requests

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if self.use_structured_output:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "pv_recommendation",
                    "strict": True,
                    "schema": PV_RECOMMENDATION_SCHEMA,
                },
            }

        resp = _requests.post(url, json=payload, headers=headers, timeout=self.timeout_s)
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"]

        # Log token usage
        usage = data.get("usage")
        if usage:
            logger.info(
                "Token usage: prompt=%s completion=%s total=%s",
                usage.get("prompt_tokens", "?"),
                usage.get("completion_tokens", "?"),
                usage.get("total_tokens", "?"),
            )
        return text

    # ── Repair attempt ───────────────────────────────────────

    def _repair(
        self,
        raw_text: str,
        errors: List[str],
        original_messages: List[Dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> str:
        """One-shot repair: ask Grok to fix its own invalid JSON."""
        repair_prompt = build_repair_prompt(raw_text, errors)
        repair_messages = [
            {"role": "system", "content": "You are a JSON repair assistant."},
            {"role": "user", "content": repair_prompt},
        ]

        logger.info("Sending repair request to xAI")
        try:
            repaired_text = self._call_with_retry(repair_messages, max_tokens, temperature)
        except Exception as exc:
            logger.error("Repair call failed: %s", exc)
            return raw_text  # return original on failure

        parsed = extract_json(repaired_text)
        if parsed is None:
            logger.error("Repair response is still not valid JSON")
            return raw_text

        is_valid, new_errors = validate_recommendation(parsed)
        if is_valid:
            logger.info("Repair succeeded – schema validation passed")
            return json.dumps(parsed, indent=2)

        logger.error("Repair attempt also failed validation: %s", new_errors)
        return json.dumps(parsed, indent=2)  # return best-effort
