"""
PV-sizing pipeline -- stitches together data extraction -> feature
engineering -> prompt building -> LLM inference -> validation -> rendering.

Uses the xAI/Grok backend for inference.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from config import WorkflowConfig
from backends.base import BaseBackend
from data_extractor import extract_all_data, can_skip_extraction
from feature_engineering import extract_all_features, format_for_llm
from prompt_builder import build_prompt, get_system_prompt
from pv_tools import run_all_tools
from renderer import render_pv_report
from schemas.pv_recommendation_schema import validate_recommendation
from utils.json_extract import extract_json

logger = logging.getLogger(__name__)


class Pipeline:
    """End-to-end PV-sizing inference pipeline."""

    def __init__(self, cfg: WorkflowConfig) -> None:
        self.cfg = cfg
        self._backend: Optional[BaseBackend] = None

    # -- Lazy initialisers ------------------------------------

    def _get_backend(self) -> BaseBackend:
        """Lazily create and cache the LLM backend."""
        if self._backend is not None:
            return self._backend

        logger.info("Initialising xAI backend")
        from grok_backend import GrokBackend

        api_key = self.cfg.xai_api_key
        if not api_key:
            raise ValueError(
                f"xAI API key not found. Set env var '{self.cfg.xai.api_key_env}'."
            )
        self._backend = GrokBackend(
            api_key=api_key,
            base_url=self.cfg.xai_base_url,
            model=self.cfg.model,
            timeout_s=self.cfg.xai_timeout_s,
            use_structured_output=self.cfg.xai_use_structured_output,
        )
        return self._backend

    # -- Follow-up chat ---------------------------------------

    def chat_followup(
        self,
        conversation: List[Dict[str, str]],
        user_question: str,
        followup_system_prompt: str = "",
    ) -> str:
        """Answer a follow-up question using the full conversation context.

        Parameters
        ----------
        conversation : list of dict
            Previous messages as ``[{"role": ..., "content": ...}, ...]``.
        user_question : str
            The new question from the user.
        followup_system_prompt : str
            System prompt for the follow-up persona.

        Returns
        -------
        str
            The assistant's free-form text response.
        """
        backend = self._get_backend()

        messages: List[Dict[str, str]] = []
        if followup_system_prompt:
            messages.append({"role": "system", "content": followup_system_prompt})

        messages.extend(conversation)
        messages.append({"role": "user", "content": user_question})

        logger.info(
            "chat_followup: %d context messages + 1 new question (%d chars)",
            len(conversation), len(user_question),
        )
        return backend.chat(
            messages,
            max_tokens=self.cfg.max_tokens,
            temperature=self.cfg.temperature,
        )

    # -- Main entry point -------------------------------------

    def run(
        self,
        name: str,
        lat: float,
        lon: float,
        *,
        save: bool = True,
        output_dir: Optional[str] = None,
        skip_extraction: bool = False,
        household_overrides: Optional[Dict[str, Any]] = None,
        budget_usd: Optional[float] = None,
        user_inputs: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run the full pipeline for a single location.

        Parameters
        ----------
        name : str
            Human-readable location name (e.g. ``"Alpine"``).
        lat, lon : float
            Geographic coordinates.
        save : bool
            Whether to persist the text report to disk.
        output_dir : str, optional
            Override the output directory from config.
        skip_extraction : bool
            If True, assume CSVs already exist under ``data/generated/<name>/``
            and skip the weather / household data extraction step.
        household_overrides : dict, optional
            Keys ``num_people``, ``num_daytime_occupants``, ``num_evs``
            forwarded to the data extraction layer.
        budget_usd : float, optional
            Override the default PV budget from config.
        user_inputs : dict, optional
            Full user inputs dict injected into the LLM prompt so the
            model can see roof_area_m2, rate_plan, panel_brand, etc.

        Returns
        -------
        dict
            ``{"feature_text": str, "raw_response": str,
              "recommendation": dict | None, "report_txt": str | None,
              "valid": bool, "errors": list}``
            ``recommendation`` has keys ``"optimal"``, ``"recommended"``,
            and ``"evidence"``.
        """
        result: Dict[str, Any] = {
            "feature_text": "",
            "raw_response": "",
            "recommendation": None,
            "tool_results": None,
            "report_txt": None,
            "valid": False,
            "errors": [],
        }

        safe_name = name.lower().replace(" ", "_").replace("-", "_")

        # 0. Data extraction (weather + household + electricity CSVs)
        logger.info("Step 0: Data extraction for %s", name)
        gen_dir = Path("data/generated") / safe_name
        if skip_extraction:
            csv_paths = {
                "weather": str(gen_dir / "weather_data.csv"),
                "household": str(gen_dir / "household_data.csv"),
                "electricity": str(gen_dir / "electricity_data.csv"),
            }
            for label, p in csv_paths.items():
                if not Path(p).exists():
                    msg = f"skip_extraction=True but {label} CSV missing: {p}"
                    result["errors"].append(msg)
                    logger.error(msg)
                    return result
        elif can_skip_extraction(
            gen_dir, lat, lon,
            self.cfg.extraction.years_back,
            household_overrides,
        ):
            logger.info("  Using cached extraction (params match)")
            csv_paths = {
                "weather": str(gen_dir / "weather_data.csv"),
                "household": str(gen_dir / "household_data.csv"),
                "electricity": str(gen_dir / "electricity_data.csv"),
            }
        else:
            csv_paths = extract_all_data(
                lat, lon, name,
                years_back=self.cfg.extraction.years_back,
                household_overrides=household_overrides,
            )

        # 1. Load CSVs
        logger.info("Step 1: Loading generated CSVs")
        df_elec = pd.read_csv(csv_paths["electricity"])
        df_weather = pd.read_csv(csv_paths["weather"])
        df_household = pd.read_csv(csv_paths["household"])

        # 2. Feature engineering
        logger.info("Step 2: Feature engineering for %s", name)
        effective_budget = budget_usd or self.cfg.user_inputs.budget_usd
        features = extract_all_features(
            df_elec,
            df_weather,
            df_household,
            pv_budget=effective_budget,
            price_per_kwh=self.cfg.features.electricity_rate_usd_kwh,
        )
        feature_text = format_for_llm(features)
        result["feature_text"] = feature_text

        # 2b. Build user_inputs from config defaults if not provided
        if user_inputs is None:
            ui_cfg = self.cfg.user_inputs
            user_inputs = {
                "latitude": lat,
                "longitude": lon,
                "num_evs": ui_cfg.num_evs,
                "num_people": ui_cfg.num_people,
                "num_daytime_occupants": ui_cfg.num_daytime_occupants,
                "budget_usd": effective_budget,
                "roof_length_m": ui_cfg.roof_length_m,
                "roof_breadth_m": ui_cfg.roof_breadth_m,
                "roof_area_m2": ui_cfg.roof_area_m2,
                "rate_plan": ui_cfg.rate_plan,
                "panel_brand": ui_cfg.panel_brand,
            }

        # 2c. Run PV tool computations (load profile, tariffs, dispatch, economics)
        tool_results = None
        logger.info("Step 2b: Running PV tool computations")
        try:
            tool_results = run_all_tools(
                latitude=lat,
                longitude=lon,
                num_evs=user_inputs.get("num_evs", 0),
                num_people=user_inputs.get("num_people", 3),
                num_daytime_occupants=user_inputs.get("num_daytime_occupants", 1),
                budget_usd=effective_budget,
                roof_length_m=user_inputs.get("roof_length_m", 8.0),
                roof_breadth_m=user_inputs.get("roof_breadth_m", 6.25),
                rate_plan=user_inputs.get("rate_plan", "TOU_DR"),
                panel_brand=user_inputs.get("panel_brand"),
            )
        except Exception as exc:
            logger.warning("PV tools failed (non-fatal): %s", exc)

        # 3. Prompt building
        logger.info("Step 3: Prompt building")
        prompt = build_prompt(
            feature_text, self.cfg.prompt,
            user_inputs=user_inputs,
            tool_results=tool_results,
        )
        system = get_system_prompt(self.cfg.prompt)

        # 5. LLM inference
        logger.info("Step 5: LLM inference (xAI/Grok)")
        backend = self._get_backend()
        raw_response = backend.generate(
            prompt=prompt,
            system=system,
            max_tokens=self.cfg.max_tokens,
            temperature=self.cfg.temperature,
        )
        result["raw_response"] = raw_response

        # 6. Parse + validate
        logger.info("Step 6: Parse and validate response")
        parsed = extract_json(raw_response)
        if parsed is None:
            result["errors"].append("Could not extract JSON from response")
            logger.error("Failed to extract JSON from LLM response")
        else:
            is_valid, errors = validate_recommendation(parsed)
            result["recommendation"] = parsed
            result["valid"] = is_valid
            result["errors"] = errors
            if not is_valid:
                logger.warning("Validation errors: %s", errors)

        # 6b. Attach tool results for UI consumption
        if tool_results:
            result["tool_results"] = tool_results

        # 7. Render report
        if result["recommendation"]:
            logger.info("Step 7: Rendering report")
            result["report_txt"] = render_pv_report(result["recommendation"])

        # 8. Save outputs
        if save and result["recommendation"]:
            out_dir = Path(output_dir or self.cfg.paths.output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)

            txt_path = out_dir / f"{safe_name}_report.txt"
            if result["report_txt"]:
                txt_path.write_text(result["report_txt"], encoding="utf-8")
                logger.info("Saved report -> %s", txt_path)

            # Also save the feature text for debugging / auditing
            feat_path = out_dir / f"{safe_name}_features.txt"
            feat_path.write_text(feature_text, encoding="utf-8")
            logger.info("Saved features -> %s", feat_path)

        return result
