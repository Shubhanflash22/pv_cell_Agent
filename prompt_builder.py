"""
Prompt builder for PV-sizing pipeline.

Assembles: feature summary + RAG passages + task instruction (with
hard rules and decision policy) into a final prompt with truncation.
"""

from __future__ import annotations

from typing import Optional

from config import PromptConfig
from schemas.pv_recommendation_schema import PV_RECOMMENDATION_SCHEMA_JSON


# ── Hard rules block ─────────────────────────────────────────

HARD_RULES = """\
### HARD RULES (you must obey all of these)

1. You must NOT introduce any numeric values that are not found in the
   FEATURES block or the RAG passages unless you explicitly label them as
   "assumption" in your output.
2. Your output must contain exactly TWO scenarios: "optimal" and "recommended".
   - "optimal"     : the technically best system that maximises energy offset
                     and long-term ROI, even if it exceeds the stated budget.
   - "recommended" : the balanced, budget-aware system the homeowner should
                     actually purchase — must NOT exceed `max_panels_within_budget`
                     when the budget is binding.
3. If `panels_for_70pct_offset` is within budget, the RECOMMENDED scenario
   must target at least 70% offset — unless nighttime load fraction is
   very high (>0.50) or the export policy makes it uneconomical.
4. The OPTIMAL scenario may target 100% offset (or higher ROI) regardless of
   the stated budget, but must not exceed roof capacity.
5. Each scenario must include its own `rationale` field (1-3 sentences
   explaining why that specific panel count was chosen).
6. Your output must be valid JSON matching the schema below. No prose,
   no markdown fences, no extra text — ONLY the JSON object.
7. Include 5–12 shared evidence entries, each quoting a specific numeric
   value from the FEATURES block or a relevant fact from RAG passages.
"""

# ── Decision policy block ────────────────────────────────────

DECISION_POLICY = """\
### DECISION POLICY (follow this algorithm)

Define from FEATURES:
  N_50     = panels_for_50pct_offset
  N_70     = panels_for_70pct_offset
  N_100    = panels_for_100pct_offset
  N_budget = max_panels_within_budget
  N_roof   = max_panels_by_roof

--- OPTIMAL scenario ---
1. Start with N_opt = N_100.
2. If N_100 > N_roof: N_opt = N_roof.
3. If payback at N_100 > 15 years: consider dropping to N_70 for better ROI;
   set N_opt = N_70 in that case.
4. Compute all derived values (kW DC, production, savings, CAPEX, payback)
   from N_opt using FEATURES values.
5. Set budget_binding = False in constraints (optimal ignores budget).

--- RECOMMENDED scenario ---
1. Start with N_rec = min(N_70, N_budget).
2. If payback at N_70 > 12 years OR risks are high: N_rec = min(N_50, N_budget).
3. If nighttime_load_fraction > 0.50: recommend storage/TOU strategy; do NOT
   oversize PV — cap N_rec at N_50.
4. If roof is binding (N_roof < N_rec): N_rec = N_roof.
5. Compute all derived values from N_rec.
6. Set budget_binding = True if N_budget constrained N_rec, else False.

Both scenarios must independently populate: panels, kw_dc,
target_offset_fraction, expected_annual_production_kwh,
annual_consumption_kwh_used, expected_annual_savings_usd,
capex_estimate_usd, payback_years_estimate, rationale,
constraints, assumptions, risks, confidence.
"""


def build_prompt(
    feature_summary: str,
    rag_block: str,
    prompt_cfg: PromptConfig,
    question: Optional[str] = None,
) -> str:
    """Assemble the full user prompt for the LLM.

    Parameters
    ----------
    feature_summary : str
        Output of ``format_feature_summary()``.
    rag_block : str
        Output of ``RAGRetriever.retrieve_block()``.
    prompt_cfg : PromptConfig
        Prompt-level configuration (max chars, etc.).
    question : str, optional
        Custom user question. Defaults to the standard PV recommendation task.

    Returns
    -------
    str
        The assembled prompt, truncated if it exceeds ``max_prompt_chars``.
    """

    if question is None:
        question = (
            "Based on the FEATURES and RAG data above, produce TWO solar panel "
            "sizing scenarios for this household:\n"
            "  1. \"optimal\"     – the technically best system (max offset / ROI).\n"
            "  2. \"recommended\" – the budget-aware, practical system to purchase.\n"
            "Follow the DECISION POLICY for each scenario. "
            "Output ONLY valid JSON matching the schema — two named objects plus shared evidence."
        )

    schema_block = (
        f"### REQUIRED OUTPUT JSON SCHEMA\n"
        f"```json\n{PV_RECOMMENDATION_SCHEMA_JSON}\n```"
    )

    parts = [
        feature_summary,
        "",
        rag_block,
        "",
        HARD_RULES,
        DECISION_POLICY,
        schema_block,
        "",
        f"### TASK\n{question}",
    ]

    prompt = "\n".join(parts)

    # Truncate if necessary (keep the tail which has instructions)
    max_chars = prompt_cfg.max_prompt_chars
    if len(prompt) > max_chars:
        # Truncate the RAG block first (it's the most compressible)
        overhead = len(prompt) - max_chars
        if len(rag_block) > overhead + 200:
            truncated_rag = rag_block[: len(rag_block) - overhead - 50] + "\n... [RAG truncated] ..."
            parts[2] = truncated_rag
            prompt = "\n".join(parts)
        else:
            # Hard truncate from the start
            prompt = prompt[-max_chars:]

    return prompt


def get_system_prompt(prompt_cfg: PromptConfig) -> str:
    """Return the system prompt from config."""
    return prompt_cfg.system_prompt.strip()
