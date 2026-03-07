#!/usr/bin/env python3
"""
SolarInvest chatbot — Gradio UI with iPhone-Weather-inspired dynamic
background, two-step flow (input form -> chat), follow-up Q&A, and
New-Chat with optional download.

Usage:
    python chatbot.py
"""

from __future__ import annotations

import logging
import tempfile
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import gradio as gr

from config import load_config, WorkflowConfig, VALID_RATE_PLANS, VALID_PANEL_BRANDS
from pipeline import Pipeline
from renderer import format_recommendation_summary, render_pv_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Global singletons ────────────────────────────────────────
_cfg: WorkflowConfig = load_config()
_pipeline: Pipeline = Pipeline(_cfg)

_LAT_RANGE = (32.0, 34.0)
_LON_RANGE = (-118.0, -116.0)

_PANEL_BRAND_CHOICES = ["Auto (optimizer chooses)"] + sorted(VALID_PANEL_BRANDS)
_RATE_PLAN_CHOICES = sorted(VALID_RATE_PLANS)

_STATIC_DIR = Path(__file__).resolve().parent / "static"

# ── Load CSS and JS from static/ ─────────────────────────────
_CSS = ""
_css_path = _STATIC_DIR / "solarinvest.css"
if _css_path.exists():
    _CSS = _css_path.read_text(encoding="utf-8")

_JS_HEAD = ""
_js_path = _STATIC_DIR / "solarinvest.js"
if _js_path.exists():
    _JS_HEAD = f"<script>\n{_js_path.read_text(encoding='utf-8')}\n</script>"


# ── Helpers ───────────────────────────────────────────────────

def _validate_inputs(
    latitude: float,
    longitude: float,
    num_evs: int,
    num_people: int,
    num_daytime_occupants: int,
    budget_usd: float,
    roof_area_m2: float,
) -> Optional[str]:
    """Return an error message if inputs are invalid, else None."""
    if not (_LAT_RANGE[0] <= latitude <= _LAT_RANGE[1]):
        return (
            f"Latitude {latitude} is outside the supported San Diego range "
            f"({_LAT_RANGE[0]}--{_LAT_RANGE[1]})."
        )
    if not (_LON_RANGE[0] <= longitude <= _LON_RANGE[1]):
        return (
            f"Longitude {longitude} is outside the supported San Diego range "
            f"({_LON_RANGE[0]}--{_LON_RANGE[1]})."
        )
    if num_people < 1:
        return "Number of people must be at least 1."
    if num_daytime_occupants < 0:
        return "Daytime occupants cannot be negative."
    if num_daytime_occupants > num_people:
        return "Daytime occupants cannot exceed total people."
    if num_evs < 0:
        return "Number of EVs cannot be negative."
    if budget_usd < 1000:
        return "Budget must be at least $1,000."
    if roof_area_m2 < 5:
        return "Roof area must be at least 5 m²."
    return None


def _build_location_name(lat: float, lon: float) -> str:
    lat_s = f"{lat:.4f}".replace(".", "_").replace("-", "m")
    lon_s = f"{lon:.4f}".replace(".", "_").replace("-", "m")
    return f"loc_{lat_s}_{lon_s}"


def _format_user_message(
    lat: float, lon: float, num_evs: int, num_people: int,
    num_daytime: int, budget: float, roof: float,
    rate: str, brand: str,
) -> str:
    return (
        f"**Location:** ({lat}, {lon})\n"
        f"**Occupants:** {num_people} total, {num_daytime} daytime\n"
        f"**EVs:** {num_evs}\n"
        f"**Roof area:** {roof} m²\n"
        f"**Budget:** ${budget:,.0f} (pre-ITC)\n"
        f"**Rate plan:** {rate}\n"
        f"**Panel preference:** {brand}"
    )


def _history_to_messages(history: List[gr.ChatMessage]) -> List[Dict[str, str]]:
    """Convert Gradio ChatMessage list to OpenAI-style dicts."""
    out: List[Dict[str, str]] = []
    for m in history:
        role = m.role if hasattr(m, "role") else "user"
        content = m.content if hasattr(m, "content") else str(m)
        out.append({"role": role, "content": content})
    return out


def _build_chat_markdown(history: List[gr.ChatMessage]) -> str:
    """Render chat history as downloadable markdown."""
    lines = [
        f"# SolarInvest Chat Export",
        f"*Exported: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
        "",
    ]
    for m in history:
        role = m.role if hasattr(m, "role") else "user"
        content = m.content if hasattr(m, "content") else str(m)
        label = "You" if role == "user" else "SolarInvest Agent"
        lines.append(f"## {label}")
        lines.append("")
        lines.append(content)
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


# ── Core callbacks ────────────────────────────────────────────

def run_recommendation(
    latitude: float,
    longitude: float,
    num_evs: int,
    num_people: int,
    num_daytime_occupants: int,
    budget_usd: float,
    roof_area_m2: float,
    rate_plan: str,
    panel_brand: str,
):
    """Run the pipeline and transition from input form to chat view.

    Returns updates for: chatbot, full_report, session_state,
    input_form (visible), chat_panel (visible), newchat_options (visible).
    """
    resolved_brand = None if panel_brand == "Auto (optimizer chooses)" else panel_brand

    user_msg = _format_user_message(
        latitude, longitude, int(num_evs), int(num_people),
        int(num_daytime_occupants), budget_usd, roof_area_m2,
        rate_plan, panel_brand,
    )

    history: List[gr.ChatMessage] = [
        gr.ChatMessage(role="user", content=user_msg),
    ]

    err = _validate_inputs(
        latitude, longitude, int(num_evs), int(num_people),
        int(num_daytime_occupants), budget_usd, roof_area_m2,
    )
    if err:
        history.append(gr.ChatMessage(role="assistant", content=f"**Input Error:** {err}"))
        return (
            history, "",
            {"user_inputs": None, "recommendation": None, "full_report": ""},
            gr.update(visible=False),
            gr.update(visible=True),
            gr.update(visible=False),
        )

    history.append(
        gr.ChatMessage(role="assistant", content="Analyzing your home and computing optimal solar system -- this may take 30-60 seconds...")
    )

    try:
        _cfg.validate()
    except ValueError as exc:
        history[-1] = gr.ChatMessage(role="assistant", content=f"**Configuration Error:** {exc}")
        return (
            history, "",
            {"user_inputs": None, "recommendation": None, "full_report": ""},
            gr.update(visible=False),
            gr.update(visible=True),
            gr.update(visible=False),
        )

    name = _build_location_name(latitude, longitude)

    household_overrides: Dict[str, Any] = {
        "num_people": int(num_people),
        "num_daytime_occupants": int(num_daytime_occupants),
        "num_evs": int(num_evs),
    }

    user_inputs: Dict[str, Any] = {
        "latitude": float(latitude),
        "longitude": float(longitude),
        "num_evs": int(num_evs),
        "num_people": int(num_people),
        "num_daytime_occupants": int(num_daytime_occupants),
        "budget_usd": float(budget_usd),
        "roof_area_m2": float(roof_area_m2),
        "rate_plan": rate_plan,
        "panel_brand": resolved_brand,
    }

    try:
        result = _pipeline.run(
            name,
            latitude,
            longitude,
            save=False,
            household_overrides=household_overrides,
            budget_usd=float(budget_usd),
            user_inputs=user_inputs,
        )
    except Exception:
        tb = traceback.format_exc()
        logger.error("Pipeline error:\n%s", tb)
        history[-1] = gr.ChatMessage(
            role="assistant",
            content=f"**Pipeline Error:**\n```\n{tb}\n```",
        )
        return (
            history, "",
            {"user_inputs": user_inputs, "recommendation": None, "full_report": ""},
            gr.update(visible=False),
            gr.update(visible=True),
            gr.update(visible=False),
        )

    if result["recommendation"] is None:
        errors = "\n".join(result["errors"]) or "Unknown error"
        history[-1] = gr.ChatMessage(
            role="assistant",
            content=f"**No recommendation produced.**\n\nErrors:\n{errors}",
        )
        return (
            history, "",
            {"user_inputs": user_inputs, "recommendation": None, "full_report": ""},
            gr.update(visible=False),
            gr.update(visible=True),
            gr.update(visible=False),
        )

    summary = format_recommendation_summary(result["recommendation"])
    full_report = result.get("report_txt", "") or ""

    if result["errors"]:
        summary += (
            "\n\n> **Validation warnings:** "
            + "; ".join(result["errors"])
        )

    history[-1] = gr.ChatMessage(role="assistant", content=summary)

    session = {
        "user_inputs": user_inputs,
        "recommendation": result["recommendation"],
        "full_report": full_report,
    }

    return (
        history,
        full_report,
        session,
        gr.update(visible=False),   # hide input form
        gr.update(visible=True),    # show chat panel
        gr.update(visible=False),   # hide newchat options
    )


def send_followup(
    user_question: str,
    history: List[gr.ChatMessage],
    session: dict,
):
    """Handle a follow-up question in the chat.

    Returns updates for: chatbot, followup_input.
    """
    if not user_question or not user_question.strip():
        return history, ""

    question = user_question.strip()

    history = list(history) + [gr.ChatMessage(role="user", content=question)]

    history.append(
        gr.ChatMessage(role="assistant", content="Thinking...")
    )

    try:
        conversation_msgs = _history_to_messages(history[:-1])

        response = _pipeline.chat_followup(
            conversation=conversation_msgs,
            user_question=question,
            followup_system_prompt=_cfg.prompt.followup_system_prompt,
        )

        history[-1] = gr.ChatMessage(role="assistant", content=response)
    except Exception:
        tb = traceback.format_exc()
        logger.error("Follow-up error:\n%s", tb)
        history[-1] = gr.ChatMessage(
            role="assistant",
            content=f"**Error processing your question:**\n```\n{tb}\n```",
        )

    return history, ""


def show_newchat_options():
    """Reveal the new-chat option buttons."""
    return gr.update(visible=True)


def new_chat_with_download(history: List[gr.ChatMessage]):
    """Download current chat as markdown, then reset to input form.

    Returns updates for: download_file, chatbot, full_report,
    session_state, input_form (visible), chat_panel (visible),
    newchat_options (visible).
    """
    md_content = _build_chat_markdown(history)

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", prefix="solarinvest_chat_",
        delete=False, encoding="utf-8",
    )
    tmp.write(md_content)
    tmp.close()

    return (
        tmp.name,                    # download file path
        [],                          # clear chatbot
        "",                          # clear full report
        {"user_inputs": None, "recommendation": None, "full_report": ""},
        gr.update(visible=True),     # show input form
        gr.update(visible=False),    # hide chat panel
        gr.update(visible=False),    # hide newchat options
    )


def new_chat_no_download():
    """Clear and return to input form without downloading.

    Returns updates for: chatbot, full_report, session_state,
    input_form (visible), chat_panel (visible), newchat_options (visible).
    """
    return (
        [],                          # clear chatbot
        "",                          # clear full report
        {"user_inputs": None, "recommendation": None, "full_report": ""},
        gr.update(visible=True),     # show input form
        gr.update(visible=False),    # hide chat panel
        gr.update(visible=False),    # hide newchat options
    )


# ── Build the Gradio app ─────────────────────────────────────

def build_app() -> gr.Blocks:
    with gr.Blocks(
        title="SolarInvest - Solar PV Sizing Advisor",
    ) as app:

        # -- Session state (persists across callbacks) --
        session_state = gr.State(
            {"user_inputs": None, "recommendation": None, "full_report": ""}
        )

        # -- Title --
        gr.Markdown(
            "# SolarInvest\n"
            "Personalised solar panel recommendations for San Diego homeowners.",
            elem_id="solarinvest-title",
        )

        # ═══════════════════════════════════════════════════════
        # STEP 1: Input form (visible by default)
        # ═══════════════════════════════════════════════════════
        with gr.Column(visible=True, elem_id="solarinvest-input-form") as input_form:

            with gr.Row():
                with gr.Column(scale=1):
                    gr.Markdown("#### Location", elem_classes="section-label")
                    latitude = gr.Number(
                        label="Latitude",
                        value=_cfg.user_inputs.latitude,
                        minimum=32.0, maximum=34.0,
                    )
                    longitude = gr.Number(
                        label="Longitude",
                        value=_cfg.user_inputs.longitude,
                        minimum=-118.0, maximum=-116.0,
                    )

                with gr.Column(scale=1):
                    gr.Markdown("#### Household", elem_classes="section-label")
                    num_people = gr.Number(
                        label="Total Occupants",
                        value=_cfg.user_inputs.num_people,
                        minimum=1, maximum=20, precision=0,
                    )
                    num_daytime_occupants = gr.Number(
                        label="Daytime Occupants (9 AM - 5 PM)",
                        value=_cfg.user_inputs.num_daytime_occupants,
                        minimum=0, maximum=20, precision=0,
                    )
                    num_evs = gr.Number(
                        label="Electric Vehicles",
                        value=_cfg.user_inputs.num_evs,
                        minimum=0, maximum=10, precision=0,
                    )

                with gr.Column(scale=1):
                    gr.Markdown("#### System & Budget", elem_classes="section-label")
                    budget = gr.Number(
                        label="Budget, pre-ITC (USD)",
                        value=_cfg.user_inputs.budget_usd,
                        minimum=1000, maximum=200000, precision=0,
                    )
                    roof_area = gr.Number(
                        label="South-Facing Roof Area (m²)",
                        value=_cfg.user_inputs.roof_area_m2,
                        minimum=5, maximum=500,
                    )
                    rate_plan = gr.Dropdown(
                        label="SDG&E Rate Plan",
                        choices=_RATE_PLAN_CHOICES,
                        value=_cfg.user_inputs.rate_plan,
                    )
                    panel_brand = gr.Dropdown(
                        label="Preferred Panel Brand",
                        choices=_PANEL_BRAND_CHOICES,
                        value="Auto (optimizer chooses)",
                    )

            submit_btn = gr.Button(
                "Get Recommendation",
                variant="primary",
                size="lg",
                elem_id="solarinvest-recommend-btn",
            )

        # ═══════════════════════════════════════════════════════
        # STEP 2: Chat panel (hidden until recommendation)
        # ═══════════════════════════════════════════════════════
        with gr.Column(visible=False, elem_id="solarinvest-chat-panel") as chat_panel:

            chatbot = gr.Chatbot(
                label="SolarInvest Agent",
                height=480,
                elem_id="solarinvest-chatbot",
            )

            with gr.Accordion("Full Report", open=False):
                full_report = gr.Textbox(
                    label="Detailed Report",
                    lines=20,
                    interactive=False,
                )

            # -- Follow-up input row --
            with gr.Row():
                followup_input = gr.Textbox(
                    placeholder="Ask a follow-up question (e.g. explain payback, compare options)...",
                    show_label=False,
                    scale=4,
                    elem_id="solarinvest-followup-input",
                )
                send_btn = gr.Button(
                    "Send",
                    variant="primary",
                    scale=1,
                    elem_id="solarinvest-send-btn",
                )

            # -- New Chat button --
            newchat_btn = gr.Button(
                "New Chat",
                variant="secondary",
                elem_id="solarinvest-newchat-btn",
            )

            # -- New Chat options (hidden until New Chat clicked) --
            with gr.Row(
                visible=False,
                elem_id="solarinvest-newchat-options",
            ) as newchat_options:
                download_btn = gr.Button(
                    "Download Chat & Start New",
                    variant="secondary",
                    elem_id="solarinvest-download-btn",
                )
                skip_btn = gr.Button(
                    "Start New Chat",
                    variant="secondary",
                    elem_id="solarinvest-newchat-nodownload-btn",
                )

            download_file = gr.File(
                label="Download",
                visible=False,
                interactive=False,
            )

        # ═══════════════════════════════════════════════════════
        # Event wiring
        # ═══════════════════════════════════════════════════════

        submit_btn.click(
            fn=run_recommendation,
            inputs=[
                latitude, longitude, num_evs, num_people,
                num_daytime_occupants, budget, roof_area,
                rate_plan, panel_brand,
            ],
            outputs=[
                chatbot, full_report, session_state,
                input_form, chat_panel, newchat_options,
            ],
        )

        send_btn.click(
            fn=send_followup,
            inputs=[followup_input, chatbot, session_state],
            outputs=[chatbot, followup_input],
        )

        followup_input.submit(
            fn=send_followup,
            inputs=[followup_input, chatbot, session_state],
            outputs=[chatbot, followup_input],
        )

        newchat_btn.click(
            fn=show_newchat_options,
            inputs=[],
            outputs=[newchat_options],
        )

        download_btn.click(
            fn=new_chat_with_download,
            inputs=[chatbot],
            outputs=[
                download_file, chatbot, full_report, session_state,
                input_form, chat_panel, newchat_options,
            ],
        )

        skip_btn.click(
            fn=new_chat_no_download,
            inputs=[],
            outputs=[
                chatbot, full_report, session_state,
                input_form, chat_panel, newchat_options,
            ],
        )

    return app


if __name__ == "__main__":
    app = build_app()
    app.launch(
        theme=gr.themes.Soft(),
        css=_CSS,
        head=_JS_HEAD,
        allowed_paths=[str(_STATIC_DIR)],
    )
