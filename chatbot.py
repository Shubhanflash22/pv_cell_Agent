#!/usr/bin/env python3
"""
SolarInvest chatbot — Gradio UI redesigned as a professional solar
planning assistant.

Layout:
  Top:    Title + description
  Left:   Input form (location, household, roof, budget)
  Right:  Recommendation card + sizing calculations (hidden until run)
  Bottom: Chat advisor for follow-up questions

Usage:
    python chatbot.py
"""

from __future__ import annotations

import logging
import re
import tempfile
import textwrap
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import gradio as gr

from config import load_config, WorkflowConfig, VALID_RATE_PLANS, VALID_PANEL_BRANDS
from pipeline import Pipeline
from renderer import (
    format_recommendation_summary,
    format_recommendation_card,
    format_sizing_calculations,
    render_pv_report,
)

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

# Background environment (dynamic sky layer — loads after main theme)
_bg_css = _STATIC_DIR / "backgroundEnvironment.css"
if _bg_css.exists():
    _CSS = _CSS + "\n\n" + _bg_css.read_text(encoding="utf-8")
_bg_js = _STATIC_DIR / "backgroundEnvironment.js"
if _bg_js.exists():
    _JS_HEAD = _JS_HEAD + f"\n<script>\n{_bg_js.read_text(encoding='utf-8')}\n</script>"


# ── Helpers ───────────────────────────────────────────────────

def _validate_inputs(
    latitude: float,
    longitude: float,
    num_evs: int,
    num_people: int,
    num_daytime_occupants: int,
    budget_usd: float,
    roof_length_m: float,
    roof_breadth_m: float,
) -> Optional[str]:
    """Return a user-friendly error message if inputs are invalid, else None."""
    if not (_LAT_RANGE[0] <= latitude <= _LAT_RANGE[1]):
        return (
            f"Latitude {latitude} is outside the supported San Diego range "
            f"({_LAT_RANGE[0]}--{_LAT_RANGE[1]}). Please enter a value between 32.0 and 34.0."
        )
    if not (_LON_RANGE[0] <= longitude <= _LON_RANGE[1]):
        return (
            f"Longitude {longitude} is outside the supported range. "
            f"Please enter a value between -118.0 and -116.0."
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
    if roof_length_m < 2:
        return "Roof length must be at least 2 m."
    if roof_breadth_m < 2:
        return "Roof breadth must be at least 2 m."
    if roof_length_m * roof_breadth_m < 5:
        return "Roof area (length x breadth) must be at least 5 m²."
    return None


def _build_location_name(lat: float, lon: float) -> str:
    lat_s = f"{lat:.4f}".replace(".", "_").replace("-", "m")
    lon_s = f"{lon:.4f}".replace(".", "_").replace("-", "m")
    return f"loc_{lat_s}_{lon_s}"


def _format_user_message(
    user_name: str,
    lat: float, lon: float, num_evs: int, num_people: int,
    num_daytime: int, budget: float,
    roof_length: float, roof_breadth: float,
    rate: str, brand: str,
) -> str:
    roof_area = roof_length * roof_breadth
    return (
        f"**Name:** {user_name}\n"
        f"**Location:** ({lat}, {lon})\n"
        f"**Occupants:** {num_people} total, {num_daytime} daytime\n"
        f"**EVs:** {num_evs}\n"
        f"**Roof:** {roof_length} m x {roof_breadth} m = {roof_area:.1f} m²\n"
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


def _strip_markdown(text: str) -> str:
    """Strip common Markdown syntax to produce plain text for PDF rendering."""
    text = re.sub(r"```[^\n]*\n(.*?)```", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"\1", text)
    text = re.sub(r"\*\*(.+?)\*\*",     r"\1", text)
    text = re.sub(r"\*(.+?)\*",         r"\1", text)
    text = re.sub(r"__(.+?)__",         r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^-{3,}$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^>\s?", "  ", text, flags=re.MULTILINE)
    text = re.sub(r"^[\*\-]\s+", "- ", text, flags=re.MULTILINE)
    return text.strip()


def _build_chat_pdf(
    history: List[gr.ChatMessage],
    session: Optional[Dict[str, Any]] = None,
) -> str:
    """Render the full chat history as a PDF and return the temp file path."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, HRFlowable, Table, TableStyle
    )
    from reportlab.lib.enums import TA_LEFT, TA_CENTER

    user_name = (session or {}).get("user_name", "Homeowner")
    safe_name = re.sub(r"[^a-zA-Z0-9_\- ]", "", user_name).strip().replace(" ", "_") or "Homeowner"
    filename = f"{safe_name}_recommendation_summary.pdf"

    tmp = tempfile.NamedTemporaryFile(
        suffix=".pdf", prefix=f"{safe_name}_", delete=False,
    )
    tmp.close()

    import os, shutil
    final_dir = tempfile.gettempdir()
    final_path = os.path.join(final_dir, filename)

    doc = SimpleDocTemplate(
        tmp.name,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2.5 * cm,
        bottomMargin=2 * cm,
    )

    NAVY   = colors.HexColor("#1a2a6c")
    AMBER  = colors.HexColor("#f7b733")
    LIGHT  = colors.HexColor("#f0f4ff")
    GREY   = colors.HexColor("#555555")
    USER_BG   = colors.HexColor("#e8f0fe")
    AGENT_BG  = colors.HexColor("#fff8e1")
    USER_BORDER  = colors.HexColor("#4a6fa5")
    AGENT_BORDER = colors.HexColor("#e8a000")

    base = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "Title", parent=base["Title"],
        textColor=NAVY, fontSize=22, spaceAfter=4, alignment=TA_CENTER,
        fontName="Helvetica-Bold",
    )
    sub_style = ParagraphStyle(
        "Sub", parent=base["Normal"],
        textColor=GREY, fontSize=9, spaceAfter=2, alignment=TA_CENTER,
    )
    section_style = ParagraphStyle(
        "Section", parent=base["Normal"],
        textColor=NAVY, fontSize=11, spaceBefore=10, spaceAfter=4,
        fontName="Helvetica-Bold",
    )
    label_style = ParagraphStyle(
        "Label", parent=base["Normal"],
        textColor=colors.white, fontSize=9, spaceBefore=0, spaceAfter=0,
        fontName="Helvetica-Bold",
    )
    user_text_style = ParagraphStyle(
        "UserText", parent=base["Normal"],
        textColor=colors.HexColor("#1a1a3e"), fontSize=9.5,
        leading=14, spaceBefore=2, spaceAfter=2,
    )
    agent_text_style = ParagraphStyle(
        "AgentText", parent=base["Normal"],
        textColor=colors.HexColor("#2d1b00"), fontSize=9.5,
        leading=14, spaceBefore=2, spaceAfter=2,
    )

    story = []

    story.append(Paragraph("SolarInvest", title_style))
    story.append(Paragraph("Solar PV Sizing Advisor — Chat Export", sub_style))
    story.append(Paragraph(
        f"Generated: {datetime.now().strftime('%A, %B %d %Y at %H:%M:%S')}",
        sub_style,
    ))
    story.append(Spacer(1, 0.3 * cm))
    story.append(HRFlowable(width="100%", thickness=2, color=AMBER))
    story.append(Spacer(1, 0.4 * cm))

    ui = (session or {}).get("user_inputs") or {}
    if ui:
        story.append(Paragraph("Session Parameters", section_style))
        meta_rows = [
            ["Location",           f"({ui.get('latitude', '?')}, {ui.get('longitude', '?')})"],
            ["Occupants",          f"{ui.get('num_people', '?')} total, {ui.get('num_daytime_occupants', '?')} daytime"],
            ["Electric Vehicles",  str(ui.get("num_evs", 0))],
            ["Roof",               f"{ui.get('roof_length_m', '?')} m x {ui.get('roof_breadth_m', '?')} m = {ui.get('roof_area_m2', '?')} m²"],
            ["Budget (pre-ITC)",   f"${ui.get('budget_usd', 0):,.0f}"],
            ["Rate Plan",          str(ui.get("rate_plan", "?"))],
            ["Panel Preference",   str(ui.get("panel_brand") or "Auto (optimizer chooses)")],
        ]
        tbl = Table(meta_rows, colWidths=[4.5 * cm, None])
        tbl.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (0, -1), LIGHT),
            ("TEXTCOLOR",    (0, 0), (0, -1), NAVY),
            ("FONTNAME",     (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTSIZE",     (0, 0), (-1, -1), 9),
            ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, LIGHT]),
            ("BOX",          (0, 0), (-1, -1), 0.5, GREY),
            ("INNERGRID",    (0, 0), (-1, -1), 0.25, colors.lightgrey),
            ("LEFTPADDING",  (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING",   (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ]))
        story.append(tbl)
        story.append(Spacer(1, 0.4 * cm))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.lightgrey))
        story.append(Spacer(1, 0.3 * cm))

    story.append(Paragraph("Conversation", section_style))
    story.append(Spacer(1, 0.2 * cm))

    for idx, m in enumerate(history):
        role    = m.role    if hasattr(m, "role")    else "user"
        content = m.content if hasattr(m, "content") else str(m)
        is_user = (role == "user")

        label_text  = user_name if is_user else "SolarInvest"
        bg          = USER_BG   if is_user else AGENT_BG
        border_col  = USER_BORDER if is_user else AGENT_BORDER
        text_style  = user_text_style if is_user else agent_text_style
        label_bg    = USER_BORDER if is_user else AGENT_BORDER

        plain = _strip_markdown(content)
        wrapped_lines = []
        for line in plain.splitlines():
            if len(line) <= 95:
                wrapped_lines.append(line)
            else:
                wrapped_lines.extend(textwrap.wrap(line, 95))
        plain_wrapped = "\n".join(wrapped_lines)

        safe = plain_wrapped.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        safe_html = safe.replace("\n", "<br/>")

        label_para = Paragraph(label_text, label_style)
        msg_para   = Paragraph(safe_html, text_style)

        msg_tbl = Table(
            [[label_para], [msg_para]],
            colWidths=["100%"],
        )
        msg_tbl.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, 0), label_bg),
            ("BACKGROUND",   (0, 1), (-1, -1), bg),
            ("BOX",          (0, 0), (-1, -1), 0.75, border_col),
            ("TOPPADDING",   (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 5),
            ("LEFTPADDING",  (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(msg_tbl)
        story.append(Spacer(1, 0.25 * cm))

    story.append(Spacer(1, 0.3 * cm))
    story.append(HRFlowable(width="100%", thickness=1, color=AMBER))
    story.append(Spacer(1, 0.15 * cm))
    story.append(Paragraph(
        "SolarInvest — AI-powered solar sizing for San Diego homeowners. "
        "This report is for informational purposes only.",
        ParagraphStyle("Footer", parent=base["Normal"],
                       textColor=GREY, fontSize=7.5, alignment=TA_CENTER),
    ))

    doc.build(story)
    shutil.move(tmp.name, final_path)
    return final_path


# ── Core callbacks ────────────────────────────────────────────

def run_recommendation(
    user_name: str,
    latitude: float,
    longitude: float,
    num_evs: int,
    num_people: int,
    num_daytime_occupants: int,
    budget_usd: float,
    roof_length_m: float,
    roof_breadth_m: float,
    rate_plan: str,
    panel_brand: str,
):
    """Run the pipeline and populate the results panel.

    Returns updates for: recommendation_card_md, sizing_md,
    full_report, chatbot, session_state,
    input_col (visible), results_col (visible), chat_section (visible),
    error_box, loading_indicator.
    """
    if not user_name or not user_name.strip():
        user_name = "Homeowner"
    user_name = user_name.strip()

    resolved_brand = None if panel_brand == "Auto (optimizer chooses)" else panel_brand

    err = _validate_inputs(
        latitude, longitude, int(num_evs), int(num_people),
        int(num_daytime_occupants), budget_usd, roof_length_m, roof_breadth_m,
    )
    if err:
        return (
            "",                          # recommendation card
            "",                          # sizing calculations
            "",                          # full report
            [],                          # chatbot
            {"user_inputs": None, "recommendation": None, "full_report": "", "user_name": user_name},
            gr.update(visible=True),     # keep input col visible
            gr.update(visible=False),    # hide results
            gr.update(visible=False),    # hide chat
            gr.update(value=f"**Please check your inputs:** {err}", visible=True),
            gr.update(visible=False),    # hide loading
        )

    user_inputs: Dict[str, Any] = {
        "latitude": float(latitude),
        "longitude": float(longitude),
        "num_evs": int(num_evs),
        "num_people": int(num_people),
        "num_daytime_occupants": int(num_daytime_occupants),
        "budget_usd": float(budget_usd),
        "roof_length_m": float(roof_length_m),
        "roof_breadth_m": float(roof_breadth_m),
        "roof_area_m2": round(float(roof_length_m) * float(roof_breadth_m), 3),
        "rate_plan": rate_plan,
        "panel_brand": resolved_brand,
    }

    household_overrides: Dict[str, Any] = {
        "num_people": int(num_people),
        "num_daytime_occupants": int(num_daytime_occupants),
        "num_evs": int(num_evs),
    }

    try:
        _cfg.validate()
    except ValueError as exc:
        return (
            "", "", "", [],
            {"user_inputs": user_inputs, "recommendation": None, "full_report": "", "user_name": user_name},
            gr.update(visible=True), gr.update(visible=False), gr.update(visible=False),
            gr.update(value=f"**Configuration error:** {exc}", visible=True),
            gr.update(visible=False),
        )

    name = _build_location_name(latitude, longitude)

    try:
        result = _pipeline.run(
            name, latitude, longitude,
            save=False,
            household_overrides=household_overrides,
            budget_usd=float(budget_usd),
            user_inputs=user_inputs,
        )
    except Exception:
        tb = traceback.format_exc()
        logger.error("Pipeline error:\n%s", tb)
        return (
            "", "", "", [],
            {"user_inputs": user_inputs, "recommendation": None, "full_report": "", "user_name": user_name},
            gr.update(visible=True), gr.update(visible=False), gr.update(visible=False),
            gr.update(value="**Something went wrong** while analyzing your home. Please try again in a moment.", visible=True),
            gr.update(visible=False),
        )

    if result["recommendation"] is None:
        return (
            "", "", "", [],
            {"user_inputs": user_inputs, "recommendation": None, "full_report": "", "user_name": user_name},
            gr.update(visible=True), gr.update(visible=False), gr.update(visible=False),
            gr.update(value="**Could not generate a recommendation.** The system may be temporarily unavailable. Please try again.", visible=True),
            gr.update(visible=False),
        )

    tool_results = result.get("tool_results")

    card_md = format_recommendation_card(result["recommendation"], tool_results)
    sizing_md = format_sizing_calculations(tool_results, user_inputs)
    full_report = result.get("report_txt", "") or ""

    user_msg = _format_user_message(
        user_name, latitude, longitude, int(num_evs), int(num_people),
        int(num_daytime_occupants), budget_usd,
        roof_length_m, roof_breadth_m, rate_plan, panel_brand,
    )

    summary = format_recommendation_summary(result["recommendation"])

    history: List[gr.ChatMessage] = [
        gr.ChatMessage(role="user", content=user_msg, metadata={"title": user_name}),
        gr.ChatMessage(role="assistant", content=summary, metadata={"title": "SolarInvest"}),
    ]

    session = {
        "user_inputs": user_inputs,
        "recommendation": result["recommendation"],
        "tool_results": tool_results,
        "full_report": full_report,
        "user_name": user_name,
    }

    return (
        card_md,                         # recommendation card
        sizing_md,                       # sizing calculations
        full_report,                     # full report textbox
        history,                         # chatbot
        session,                         # session state
        gr.update(visible=True),         # keep input col (left side)
        gr.update(visible=True),         # show results col (right side)
        gr.update(visible=True),         # show chat section
        gr.update(value="", visible=False),  # clear errors
        gr.update(visible=False),        # hide loading
    )


def send_followup(
    user_question: str,
    history: List[gr.ChatMessage],
    session: dict,
):
    """Handle a follow-up question from the advisor chat.

    Returns updates for: chatbot, followup_input.
    """
    if not user_question or not user_question.strip():
        return history, ""

    question = user_question.strip()
    user_name = (session or {}).get("user_name", "You")

    history = list(history) + [
        gr.ChatMessage(role="user", content=question, metadata={"title": user_name})
    ]
    history.append(
        gr.ChatMessage(role="assistant", content="Let me look into that for you...", metadata={"title": "SolarInvest"})
    )

    try:
        conversation_msgs = _history_to_messages(history[:-1])

        response = _pipeline.chat_followup(
            conversation=conversation_msgs,
            user_question=question,
            followup_system_prompt=_cfg.prompt.followup_system_prompt,
        )

        history[-1] = gr.ChatMessage(role="assistant", content=response, metadata={"title": "SolarInvest"})
    except Exception:
        tb = traceback.format_exc()
        logger.error("Follow-up error:\n%s", tb)
        history[-1] = gr.ChatMessage(
            role="assistant",
            content="I'm sorry, I wasn't able to process that question. Could you try rephrasing it?",
            metadata={"title": "SolarInvest"},
        )

    return history, ""


def show_newchat_options():
    return gr.update(visible=True)


def _transcribe_audio(audio: Union[str, tuple, None]) -> str:
    """Transcribe audio file to text. Returns empty string on failure."""
    if audio is None:
        return ""
    path = audio
    if isinstance(audio, tuple):
        path = audio[0] if audio else None
    if not path or not Path(str(path)).exists():
        return ""
    path = Path(str(path))
    wav_path = path
    try:
        if path.suffix.lower() not in (".wav", ".wave"):
            try:
                from pydub import AudioSegment
                seg = AudioSegment.from_file(str(path))
                wav_path = path.with_suffix(".wav")
                seg.export(str(wav_path), format="wav")
            except Exception:
                wav_path = path
        import speech_recognition as sr
        r = sr.Recognizer()
        with sr.AudioFile(str(wav_path)) as source:
            audio_data = r.record(source)
        return r.recognize_google(audio_data, language="en-US")
    except Exception as e:
        logger.warning("Transcription failed: %s", e)
        return ""


def _on_audio_recorded(audio: Union[str, tuple, None], current_text: str) -> str:
    """When user finishes recording, transcribe and fill the textbox."""
    if audio is None:
        return current_text
    transcript = _transcribe_audio(audio)
    if transcript:
        return (current_text + " " + transcript).strip() if current_text else transcript
    return current_text


def enter_app(name: str):
    """Transition from landing page to main chatbot page."""
    resolved = (name or "").strip() or "Homeowner"
    return (
        resolved,
        gr.update(visible=False),  # hide landing page
        gr.update(visible=True),   # show main app page
    )


def export_chat_pdf(
    history: List[gr.ChatMessage],
    session: dict,
):
    if not history:
        return gr.update(visible=False), "Nothing to export yet."
    try:
        pdf_path = _build_chat_pdf(history, session)
        return gr.update(value=pdf_path, visible=True), "PDF ready — downloading..."
    except Exception:
        tb = traceback.format_exc()
        logger.error("PDF export error:\n%s", tb)
        return gr.update(visible=False), f"Export failed — please try again."


def new_chat_with_download(history: List[gr.ChatMessage], session: dict):
    try:
        pdf_path = _build_chat_pdf(history, session)
        file_update = gr.update(value=pdf_path, visible=True)
    except Exception:
        tb = traceback.format_exc()
        logger.error("PDF export error:\n%s", tb)
        file_update = gr.update(visible=False)

    return (
        file_update,
        "",
        [],
        "",
        "",
        "",
        {"user_inputs": None, "recommendation": None, "full_report": "", "user_name": "Homeowner"},
        gr.update(visible=True),
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(visible=False),
    )


_JS_RELOAD = "() => { setTimeout(() => window.location.reload(), 300); }"


def new_chat_no_download():
    return (
        [],
        "",
        "",
        "",
        {"user_inputs": None, "recommendation": None, "full_report": "", "user_name": "Homeowner"},
        gr.update(visible=True),
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(visible=False),
    )


# ── Build the Gradio app ─────────────────────────────────────

def build_app() -> gr.Blocks:
    with gr.Blocks(
        title="SolarInvest - Solar PV Sizing Advisor",
    ) as app:

        user_name_state = gr.State("Homeowner")
        session_state = gr.State(
            {"user_inputs": None, "recommendation": None, "full_report": "", "user_name": "Homeowner"}
        )

        # ── Page 1: Name capture landing page ────────────────
        with gr.Column(visible=True, elem_id="solarinvest-landing-page") as landing_page:
            gr.Markdown(
                "# SolarInvest\n"
                "A solar planning assistant for San Diego homeowners.",
                elem_id="solarinvest-landing-title",
            )
            gr.Markdown(
                "Welcome! Enter your name to begin a personalized recommendation.",
                elem_id="solarinvest-landing-subtitle",
            )
            landing_name = gr.Textbox(
                label="Your Name",
                placeholder="Enter your name",
                value="",
                interactive=True,
                elem_id="solarinvest-landing-name",
            )
            landing_start_btn = gr.Button(
                "Get my Recommendation",
                variant="primary",
                size="lg",
                elem_id="solarinvest-landing-cta",
            )

        # ── Page 2: Main chatbot page ─────────────────────────
        with gr.Column(visible=False, elem_id="solarinvest-main-page") as main_page:
            # ── Title banner ──────────────────────────────────
            gr.Markdown(
                "# SolarInvest\n"
                "Your personal solar investment planning tool for San Diego. "
                "Enter your home details below, and we'll calculate the optimal solar system for your roof, budget, and lifestyle.",
                elem_id="solarinvest-title",
            )

            # ── Error display (hidden by default) ─────────────
            error_box = gr.Markdown(
                value="", visible=False,
                elem_id="solarinvest-error-box",
            )

            # ── Loading indicator (hidden by default) ─────────
            loading_indicator = gr.Markdown(
                value="**Analyzing your home and computing optimal solar system...** This typically takes 30–60 seconds.",
                visible=False,
                elem_id="solarinvest-loading",
            )

            # ═══════════════════════════════════════════════════
            # Main two-column layout: Input (left) + Results (right)
            # ═══════════════════════════════════════════════════
            with gr.Row(elem_id="solarinvest-main-row"):

                # ── LEFT COLUMN: Input form ───────────────────
                with gr.Column(scale=2, visible=True, elem_id="solarinvest-input-form") as input_col:

                    gr.Markdown("#### Location", elem_classes="section-label")
                    with gr.Row():
                        latitude = gr.Number(
                            label="Latitude",
                            value=_cfg.user_inputs.latitude,
                            minimum=32.0, maximum=34.0,
                            interactive=True,
                        )
                        longitude = gr.Number(
                            label="Longitude",
                            value=_cfg.user_inputs.longitude,
                            minimum=-118.0, maximum=-116.0,
                            interactive=True,
                        )

                    gr.Markdown("#### Household", elem_classes="section-label")
                    num_people = gr.Number(
                        label="Total Occupants",
                        value=_cfg.user_inputs.num_people,
                        minimum=1, maximum=20, precision=0,
                        interactive=True,
                    )
                    num_daytime_occupants = gr.Number(
                        label="Daytime Occupants (9 AM–5 PM)",
                        value=_cfg.user_inputs.num_daytime_occupants,
                        minimum=0, maximum=20, precision=0,
                        interactive=True,
                    )
                    num_evs = gr.Number(
                        label="Electric Vehicles",
                        value=_cfg.user_inputs.num_evs,
                        minimum=0, maximum=10, precision=0,
                        interactive=True,
                    )

                    gr.Markdown("#### Roof Dimensions", elem_classes="section-label")
                    with gr.Row():
                        roof_length = gr.Number(
                            label="Length (m)",
                            value=_cfg.user_inputs.roof_length_m,
                            minimum=2, maximum=100,
                            interactive=True,
                        )
                        roof_breadth = gr.Number(
                            label="Breadth (m)",
                            value=_cfg.user_inputs.roof_breadth_m,
                            minimum=2, maximum=100,
                            interactive=True,
                        )

                    gr.Markdown("#### System & Budget", elem_classes="section-label")
                    budget = gr.Number(
                        label="Budget, pre-ITC (USD)",
                        value=_cfg.user_inputs.budget_usd,
                        minimum=1000, maximum=200000, precision=0,
                        interactive=True,
                    )
                    rate_plan = gr.Dropdown(
                        label="SDG&E Rate Plan",
                        choices=_RATE_PLAN_CHOICES,
                        value=_cfg.user_inputs.rate_plan,
                        interactive=True,
                    )
                    panel_brand = gr.Dropdown(
                        label="Preferred Panel Brand",
                        choices=_PANEL_BRAND_CHOICES,
                        value="Auto (optimizer chooses)",
                        interactive=True,
                    )

                    submit_btn = gr.Button(
                        "Generate Recommendation",
                        variant="primary",
                        size="lg",
                        elem_id="solarinvest-recommend-btn",
                    )

                # ── RIGHT COLUMN: Results (hidden until run) ──
                with gr.Column(scale=3, visible=False, elem_id="solarinvest-results-col") as results_col:

                    gr.Markdown("## Your Solar Recommendation", elem_id="solarinvest-results-title")

                    recommendation_card_md = gr.Markdown(
                        value="",
                        elem_id="solarinvest-recommendation-card",
                    )

                    with gr.Accordion("Sizing Calculations — How We Got These Numbers", open=False, elem_id="solarinvest-sizing-accordion"):
                        sizing_md = gr.Markdown(
                            value="",
                            elem_id="solarinvest-sizing-details",
                        )

                    with gr.Accordion("Full Report", open=False):
                        full_report = gr.Textbox(
                            label="Detailed Report",
                            lines=20,
                            interactive=False,
                        )

            # ═══════════════════════════════════════════════════
            # Chat advisor section (hidden until recommendation)
            # ═══════════════════════════════════════════════════
            with gr.Column(visible=False, elem_id="solarinvest-chat-section") as chat_section:

                gr.Markdown(
                    "### Ask Your Solar Advisor\n"
                    "Have questions about your recommendation? Ask below — for example: "
                    "*\"Why did you recommend this many panels?\"*, "
                    "*\"What if my budget is $20k?\"*, or "
                    "*\"Would a battery make sense for my house?\"*",
                    elem_id="solarinvest-chat-header",
                )

                chatbot = gr.Chatbot(
                    label="SolarInvest Advisor",
                    height=400,
                    elem_id="solarinvest-chatbot",
                )

                with gr.Row():
                    followup_input = gr.Textbox(
                        placeholder="Ask a question about your solar recommendation...",
                        show_label=False,
                        scale=8,
                        elem_id="solarinvest-followup-input",
                    )
                    audio_mic = gr.Audio(
                        sources=["microphone"],
                        type="filepath",
                        show_label=False,
                        elem_id="solarinvest-audio-mic",
                        scale=1,
                    )
                    send_btn = gr.Button(
                        "Ask",
                        variant="primary",
                        scale=1,
                        elem_id="solarinvest-send-btn",
                    )

                with gr.Row(elem_id="solarinvest-action-row"):
                    export_pdf_btn = gr.Button(
                        "Export Chat as PDF",
                        variant="secondary",
                        scale=2,
                        elem_id="solarinvest-export-btn",
                    )
                    newchat_btn = gr.Button(
                        "New Analysis",
                        variant="secondary",
                        scale=1,
                        elem_id="solarinvest-newchat-btn",
                    )

                export_status = gr.Markdown(
                    value="", visible=True,
                    elem_id="solarinvest-export-status",
                )

                with gr.Row(
                    visible=False,
                    elem_id="solarinvest-newchat-options",
                ) as newchat_options:
                    download_btn = gr.Button(
                        "Download PDF & Start New",
                        variant="secondary",
                        elem_id="solarinvest-download-btn",
                    )
                    skip_btn = gr.Button(
                        "Start New Without Downloading",
                        variant="secondary",
                        elem_id="solarinvest-newchat-nodownload-btn",
                    )

                download_file = gr.File(
                    label="Chat Export (PDF)",
                    visible=False,
                    interactive=False,
                    elem_id="solarinvest-download-file",
                )

        # ═══════════════════════════════════════════════════════
        # Event wiring
        # ═══════════════════════════════════════════════════════
        landing_start_btn.click(
            fn=enter_app,
            inputs=[landing_name],
            outputs=[user_name_state, landing_page, main_page],
        )

        landing_name.submit(
            fn=enter_app,
            inputs=[landing_name],
            outputs=[user_name_state, landing_page, main_page],
        )

        submit_btn.click(
            fn=lambda: gr.update(visible=True),
            inputs=[],
            outputs=[loading_indicator],
        ).then(
            fn=run_recommendation,
            inputs=[
                user_name_state,
                latitude, longitude, num_evs, num_people,
                num_daytime_occupants, budget,
                roof_length, roof_breadth,
                rate_plan, panel_brand,
            ],
            outputs=[
                recommendation_card_md, sizing_md,
                full_report, chatbot, session_state,
                input_col, results_col, chat_section,
                error_box, loading_indicator,
            ],
        )

        send_btn.click(
            fn=send_followup,
            inputs=[followup_input, chatbot, session_state],
            outputs=[chatbot, followup_input],
        )

        audio_mic.change(
            fn=_on_audio_recorded,
            inputs=[audio_mic, followup_input],
            outputs=[followup_input],
        )

        followup_input.submit(
            fn=send_followup,
            inputs=[followup_input, chatbot, session_state],
            outputs=[chatbot, followup_input],
        )

        export_pdf_btn.click(
            fn=export_chat_pdf,
            inputs=[chatbot, session_state],
            outputs=[download_file, export_status],
        )

        newchat_btn.click(
            fn=show_newchat_options,
            inputs=[],
            outputs=[newchat_options],
        )

        download_btn.click(
            fn=new_chat_with_download,
            inputs=[chatbot, session_state],
            outputs=[
                download_file, export_status, chatbot,
                recommendation_card_md, sizing_md, full_report,
                session_state,
                input_col, results_col, chat_section, newchat_options,
            ],
            js="() => { setTimeout(() => window.location.reload(), 2000); }",
        )

        skip_btn.click(
            fn=new_chat_no_download,
            inputs=[],
            outputs=[
                chatbot,
                recommendation_card_md, sizing_md, full_report,
                session_state,
                input_col, results_col, chat_section, newchat_options,
            ],
            js=_JS_RELOAD,
        )

    return app


if __name__ == "__main__":
    app = build_app()
    app.launch(
        theme=gr.themes.Soft(),
        css=_CSS,
        head=_JS_HEAD,
        allowed_paths=[str(_STATIC_DIR)],
        ssr_mode=False,
    )
