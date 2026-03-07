# 06 — Grok Backend (`grok_backend.py` + `backends/base.py`)

## Purpose

The Grok backend is the **LLM inference engine** — it sends the assembled prompt to xAI's Grok model and returns the response text. It handles:

- **API communication** via the OpenAI-compatible SDK.
- **Retry with exponential backoff** on transient errors (429 rate limits, 5xx server errors).
- **Structured output** (optional JSON schema enforcement).
- **Automatic repair** — if the response fails schema validation, it sends a one-shot repair request.
- **Fallback** to raw HTTP `requests` if the `openai` package is not installed.

---

## File: `backends/base.py` (Abstract Base Class)

### What It Is

An abstract base class that defines the **interface** every LLM backend must implement. This enables swapping backends without changing pipeline code.

```python
class BaseBackend(abc.ABC):
    @abc.abstractmethod
    def generate(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> str:
        """Send a prompt to the model and return the assistant text."""
```

### Interface Contract

| Parameter | Type | Description |
|-----------|------|-------------|
| `prompt` | str | The user/assembled prompt (from `build_prompt()`) |
| `system` | str | System-level instruction (from `get_system_prompt()`) |
| `max_tokens` | int | Maximum tokens in the response |
| `temperature` | float | Sampling temperature (0.0–2.0) |
| **Returns** | str | The model's response text |

Any class that extends `BaseBackend` must implement `generate()` with this signature.

---

## File: `grok_backend.py` (Implementation)

### Class: `GrokBackend`

```python
class GrokBackend(BaseBackend):
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.x.ai/v1",
        model: str = "grok-4-1-fast-reasoning",
        timeout_s: float = 3600.0,
        use_structured_output: bool = True,
    ) -> None:
```

### Constructor Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `api_key` | str | (required) | xAI API key from `XAI_API_KEY` env var |
| `base_url` | str | `https://api.x.ai/v1` | xAI API base URL |
| `model` | str | `grok-4-1-fast-reasoning` | Model identifier |
| `timeout_s` | float | `3600.0` | HTTP timeout (now set to `120` via config) |
| `use_structured_output` | bool | `True` | Include JSON schema in request (now `false` via config) |

### SDK Initialisation

The constructor attempts to import and create an OpenAI SDK client:

```python
from openai import OpenAI

self._client = OpenAI(
    api_key=self.api_key,
    base_url=self.base_url,
    timeout=self.timeout_s,
)
self._use_sdk = True
```

If `openai` is not installed, it falls back to raw HTTP requests via the `requests` library.

---

## The `generate()` Method

This is the main entry point called by the pipeline:

```python
def generate(self, prompt, system="", max_tokens=2048, temperature=0.2) -> str:
```

### Execution Flow

```
generate(prompt, system, max_tokens, temperature)
    │
    ├── 1. Build messages array
    │      [{"role": "system", "content": system},
    │       {"role": "user", "content": prompt}]
    │
    ├── 2. Log: model, prompt length, structured output mode
    │
    ├── 3. Call _call_with_retry(messages, max_tokens, temperature)
    │      └── Up to 4 attempts (1 initial + 3 retries)
    │      └── Returns raw response text
    │
    ├── 4. Log: response length, latency
    │
    └── 5. If structured output is DISABLED (current config):
    │      └── Return raw text directly
    │
         If structured output is ENABLED:
         ├── 5a. Parse JSON from response
         ├── 5b. Validate against schema
         ├── 5c. If valid → return formatted JSON
         └── 5d. If invalid → attempt repair → return best-effort
```

### When Structured Output is Disabled (Current Config)

With `use_structured_output: false`, the backend:
1. Sends the prompt **without** a `response_format` parameter.
2. Returns the **raw text** from the LLM.
3. JSON extraction and validation happen **downstream** in the pipeline (Step 6).

This is faster because the model doesn't need to conform to a schema server-side.

### When Structured Output is Enabled

With `use_structured_output: true`, the backend:
1. Includes a `response_format` parameter in the API request:
```python
kwargs["response_format"] = {
    "type": "json_schema",
    "json_schema": {
        "name": "pv_recommendation",
        "strict": True,
        "schema": PV_RECOMMENDATION_SCHEMA,
    },
}
```
2. The xAI server enforces the schema, ensuring the output conforms.
3. The backend still validates locally and attempts repair if needed.

---

## Retry Logic

### Configuration

```python
_MAX_RETRIES = 3           # Maximum retry attempts
_BASE_BACKOFF_S = 2.0      # Base delay (seconds)
_MAX_BACKOFF_S = 30.0      # Maximum delay cap
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
```

### Backoff Formula

$$\text{delay} = \min(2.0 \times 2^{\text{attempt}}, 30.0) + \text{jitter}$$

Where jitter is a random value between 0 and 25% of the computed delay.

| Attempt | Base Delay | With Max Jitter | Total Range |
|---------|-----------|-----------------|-------------|
| 0 | 2.0s | +0.5s | 2.0–2.5s |
| 1 | 4.0s | +1.0s | 4.0–5.0s |
| 2 | 8.0s | +2.0s | 8.0–10.0s |
| 3 | 16.0s | +4.0s | 16.0–20.0s |

### Retry Decision

```python
for attempt in range(_MAX_RETRIES + 1):
    try:
        return self._call_sdk(messages, max_tokens, temperature)
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        if status and int(status) == 401:
            raise                    # Auth errors: never retry
        if attempt < _MAX_RETRIES:
            time.sleep(_backoff(attempt))  # Retryable: wait and retry
        else:
            raise                    # Max retries exceeded
```

### What Gets Retried

| Status Code | Meaning | Retried? |
|-------------|---------|----------|
| 401 | Unauthorized (bad API key) | ❌ Never |
| 429 | Rate limited | ✅ Yes |
| 500 | Internal server error | ✅ Yes |
| 502 | Bad gateway | ✅ Yes |
| 503 | Service unavailable | ✅ Yes |
| 504 | Gateway timeout | ✅ Yes |
| Connection error | Network issue | ✅ Yes |
| Other | Unknown error | ✅ Yes |

---

## SDK vs Raw Requests

### OpenAI SDK Path (`_call_sdk`)

```python
def _call_sdk(self, messages, max_tokens, temperature) -> str:
    kwargs = {
        "model": self.model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    # Optionally add response_format for structured output
    resp = self._client.chat.completions.create(**kwargs)
    text = resp.choices[0].message.content or ""
    return text
```

Also logs token usage if available:
```
Token usage: prompt=3456 completion=1234 total=4690
```

### Raw Requests Path (`_call_requests`)

Used only if `openai` package is not installed:

```python
def _call_requests(self, messages, max_tokens, temperature) -> str:
    url = f"{self.base_url}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {self.api_key}",
    }
    payload = {
        "model": self.model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=self.timeout_s)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]
```

---

## Automatic Repair

If structured output validation fails, the backend sends a **one-shot repair request**:

### Repair Flow

```
Original response fails validation
    │
    ├── 1. Build repair prompt (from build_repair_prompt())
    │      Contains: error list + schema + original response
    │
    ├── 2. Send repair request to xAI
    │      System: "You are a JSON repair assistant."
    │      User: repair prompt
    │
    ├── 3. Parse repaired response
    │
    ├── 4. Validate repaired response
    │
    └── 5. If valid → return repaired JSON
         If invalid → return best-effort JSON (original parsed)
         If parse fails → return original raw text
```

### Repair Prompt Format

```
Your previous output did not match the required JSON schema.

### Errors
  - [optimal] Missing required field: 'constraints'
  - [recommended] 'confidence' should be between 0 and 1

### Schema
```json
{ ... full schema ... }
```

### Your previous output
```json
{ ... original response ... }
```

Please output ONLY corrected JSON matching the schema exactly.
No prose, no markdown fences—just the JSON object.
```

### When Repair Is Used

| Structured Output | Validation | Result |
|-------------------|-----------|--------|
| `true` | Passes | Return formatted JSON directly |
| `true` | Fails | Attempt repair → return best-effort |
| `false` | N/A | Return raw text (validation happens in pipeline) |

---

## Logging

The backend provides detailed logging at INFO level:

```
GrokBackend: using OpenAI SDK (base_url=https://api.x.ai/v1)
GrokBackend.generate  model=grok-3-fast  prompt_chars=8234  structured=False
GrokBackend response  chars=2156  latency=12.3s
Token usage: prompt=2345 completion=567 total=2912
```

On errors:
```
xAI call failed (attempt 1/4): RateLimitError – retrying in 2.3s
Schema validation failed (3 errors) – attempting repair
Repair succeeded – schema validation passed
```

---

## Configuration Impact

| Config Parameter | Effect on Backend |
|-----------------|-------------------|
| `llm.model` | Which Grok model is called |
| `llm.max_tokens` | Max response length |
| `llm.temperature` | Sampling randomness |
| `xai.use_structured_output` | Whether to send schema in request |
| `xai.timeout_s` | HTTP timeout (120s for fast models) |
| `xai.api_key_env` | Which env var holds the API key |

---

## Model Selection Guide

| Model | Speed | Quality | Use Case |
|-------|-------|---------|----------|
| `grok-3-fast` | 5–30s | Good | **Current default** — fast, reliable |
| `grok-3-mini-fast` | 2–10s | Moderate | Quick testing, lower quality |
| `grok-4-1-fast-reasoning` | 60–300s | Excellent | Deep reasoning, but very slow |

Switch models by editing `config.yaml`:
```yaml
llm:
  model: grok-3-fast    # change this
```
