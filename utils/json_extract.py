"""
Utility to extract JSON from LLM responses that may be wrapped in
markdown code fences, prose, or other noise.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Try to extract the first valid JSON object from *text*.

    Handles common LLM quirks:
    - JSON wrapped in ```json ... ``` fences
    - Leading/trailing prose around a JSON block
    - Trailing commas (basic cleanup)

    Returns the parsed dict, or ``None`` if extraction fails.
    """
    if not text or not text.strip():
        return None

    # 1. Try direct parse first (fastest path)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. Try extracting from markdown code fences
    fence_pattern = r"```(?:json)?\s*\n?(.*?)\n?\s*```"
    matches = re.findall(fence_pattern, text, re.DOTALL)
    for match in matches:
        try:
            return json.loads(match)
        except json.JSONDecodeError:
            continue

    # 3. Try to find the outermost { ... } block
    brace_start = text.find("{")
    if brace_start == -1:
        return None

    # Walk forward to find the matching closing brace
    depth = 0
    in_string = False
    escape_next = False
    for i in range(brace_start, len(text)):
        ch = text[i]
        if escape_next:
            escape_next = False
            continue
        if ch == "\\":
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[brace_start : i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    # Try light cleanup: trailing commas
                    cleaned = re.sub(
                        r",\s*([\]}])", r"\1", candidate
                    )
                    try:
                        return json.loads(cleaned)
                    except json.JSONDecodeError:
                        return None

    return None
