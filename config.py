"""
Centralised configuration for the PV-sizing workflow.

Loads ``config.yaml`` and exposes a validated ``WorkflowConfig`` dataclass.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parent
_DEFAULT_CONFIG = _PROJECT_ROOT / "config.yaml"

# ── Valid backend identifier ─────────────────────────────────
VALID_BACKENDS = {"xai"}


# ── Nested config sections ───────────────────────────────────
@dataclass
class LLMConfig:
    backend: str = "xai"
    model: str = "grok-4-1-fast-non-reasoning"
    host: str = "https://api.x.ai/v1"
    max_tokens: int = 6144
    temperature: float = 0.1


@dataclass
class XAIConfig:
    api_key_env: str = "XAI_API_KEY"
    use_structured_output: bool = False
    response_format: str = "json_schema"
    timeout_s: float = 120.0


@dataclass
class FeatureConfig:
    panel_watt_peak: float = 400.0
    system_derate: float = 0.82
    cost_per_watt_usd: float = 3.00
    electricity_rate_usd_kwh: float = 0.35
    annual_degradation: float = 0.005
    system_lifetime_years: int = 25


@dataclass
class PromptConfig:
    max_prompt_chars: int = 24000
    system_prompt: str = (
        "You are an expert solar-energy analyst specializing in "
        "residential photovoltaic system sizing for San Diego, California. "
        "You must only use the numeric data provided in the FEATURES block "
        "and the TOOL RESULTS block. Do not invent numbers."
    )
    followup_system_prompt: str = (
        "You are SolarInvest Agent — a helpful solar-energy advisor for "
        "San Diego homeowners. Answer the user's follow-up question based "
        "ONLY on the recommendation, tool results, and conversation already "
        "provided. Do NOT invent numbers, costs, or facts. If the answer "
        "is not available in the context, say \"I don't have that information "
        "in the current recommendation.\" Be concise, accurate, and helpful."
    )


@dataclass
class PathsConfig:
    data_dir: str = "data"
    output_dir: str = "outputs"
    locations_file: str = "data/locations.csv"


@dataclass
class ExtractionConfig:
    years_back: int = 2  # weather history; 2 = faster, 5 = more accurate


VALID_RATE_PLANS = {"TOU_DR", "TOU_DR1", "TOU_DR2"}

VALID_PANEL_BRANDS = {
    "REC Group", "JA Solar", "Trina Solar", "Canadian Solar",
    "Silfab Solar", "Jinko Solar", "LONGi Solar", "Maxeon Solar",
    "Aiko Solar",
}


@dataclass
class UserInputsConfig:
    latitude: float = 32.7157
    longitude: float = -117.1611
    num_evs: int = 0
    num_people: int = 2
    num_daytime_occupants: int = 1
    budget_usd: float = 25000.0
    roof_length_m: float = 8.0
    roof_breadth_m: float = 6.25
    rate_plan: str = "TOU_DR"
    panel_brand: Optional[str] = None

    @property
    def roof_area_m2(self) -> float:
        return round(self.roof_length_m * self.roof_breadth_m, 3)


# ── Top-level config ─────────────────────────────────────────
@dataclass
class WorkflowConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    xai: XAIConfig = field(default_factory=XAIConfig)
    features: FeatureConfig = field(default_factory=FeatureConfig)
    prompt: PromptConfig = field(default_factory=PromptConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    extraction: ExtractionConfig = field(default_factory=ExtractionConfig)
    user_inputs: UserInputsConfig = field(default_factory=UserInputsConfig)

    # ── Convenience accessors ────────────────────────────────
    @property
    def backend(self) -> str:
        return self.llm.backend

    @property
    def model(self) -> str:
        return self.llm.model

    @property
    def host(self) -> str:
        return self.llm.host

    @property
    def max_tokens(self) -> int:
        return self.llm.max_tokens

    @property
    def temperature(self) -> float:
        return self.llm.temperature

    @property
    def xai_api_key(self) -> Optional[str]:
        """Resolve xAI API key from environment."""
        return os.environ.get(self.xai.api_key_env)

    @property
    def xai_base_url(self) -> str:
        return self.host

    @property
    def xai_use_structured_output(self) -> bool:
        return self.xai.use_structured_output

    @property
    def xai_timeout_s(self) -> float:
        return self.xai.timeout_s

    # ── Validation ───────────────────────────────────────────
    def validate(self) -> None:
        """Raise ``ValueError`` if the configuration is inconsistent."""
        if self.backend not in VALID_BACKENDS:
            raise ValueError(
                f"backend must be one of {VALID_BACKENDS}, got '{self.backend}'"
            )

        if self.backend == "xai" and not self.xai_api_key:
            raise ValueError(
                f"backend is 'xai' but env var '{self.xai.api_key_env}' is not set. "
                "Export your xAI API key first."
            )

        if self.max_tokens < 1:
            raise ValueError("max_tokens must be >= 1")

        if not (0.0 <= self.temperature <= 2.0):
            raise ValueError("temperature must be between 0.0 and 2.0")


# ── Loader ───────────────────────────────────────────────────

def _dict_to_dataclass(cls, data: Dict[str, Any]):
    """Recursively convert a dict into a dataclass, ignoring extra keys."""
    if data is None:
        return cls()
    fieldnames = {f.name for f in cls.__dataclass_fields__.values()}
    filtered = {k: v for k, v in data.items() if k in fieldnames}
    return cls(**filtered)


def load_config(path: str | Path | None = None) -> WorkflowConfig:
    """Load and validate configuration from a YAML file.

    Parameters
    ----------
    path : str or Path, optional
        Path to ``config.yaml``.  Defaults to the file next to this module.

    Returns
    -------
    WorkflowConfig
    """
    path = Path(path) if path else _DEFAULT_CONFIG
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, "r") as f:
        raw: Dict[str, Any] = yaml.safe_load(f) or {}

    cfg = WorkflowConfig(
        llm=_dict_to_dataclass(LLMConfig, raw.get("llm")),
        xai=_dict_to_dataclass(XAIConfig, raw.get("xai")),
        features=_dict_to_dataclass(FeatureConfig, raw.get("features")),
        prompt=_dict_to_dataclass(PromptConfig, raw.get("prompt")),
        paths=_dict_to_dataclass(PathsConfig, raw.get("paths")),
        extraction=_dict_to_dataclass(ExtractionConfig, raw.get("extraction")),
        user_inputs=_dict_to_dataclass(UserInputsConfig, raw.get("user_inputs")),
    )
    return cfg
