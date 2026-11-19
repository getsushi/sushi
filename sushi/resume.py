"""Render the profile as a resume — HTML preview and a downloadable PDF.

Mirrors Tsenta's resume builder: a couple of templates, one-page friendly, built
from the structured Profile. The PDF uses reportlab (already a dependency).
"""
from __future__ import annotations

import html
import re
import time
from pathlib import Path

from .config import ensure_dirs, resumes_dir
from .models import Profile

TEMPLATES = ["Standard", "Jake"]


def _contact_line(p: Profile) -> str:
    bits = [p.location, p.email, p.phone, p.linkedin, p.github, p.website]
    return "  ·  ".join(b for b in bits if b)


def resume_html(p: Profile, *, accent: str = "#14432f") -> str:
    """Preview HTML for the in-app resume view."""
    def esc(s: str) -> str:
        return html.escape(s or "")

    edu = "".join(f"<div style='margin-bottom:4px'><b>{esc(e)}</b></div>" for e in p.education)
    exp = ""
    for e in p.experience:
        hl = "".join(f"<li>{esc(h)}</li>" for h in e.highlights)
        period = f"{esc(e.start)} – {esc(e.end) or 'Present'}"
        exp += (f"<div style='margin-bottom:9px'>"
                f"<div><b>{esc(e.company)}</b> &nbsp;|&nbsp; {esc(e.title)} "
                f"<span style='color:#6b7280;float:right'>{period}</span></div>"
                f"<ul style='margin:4px 0 0 18px;padding:0'>{hl}</ul></div>")
    skills = ", ".join(p.skills)
    return f"""
    <div style='font-family:Georgia,serif;color:#14181f'>
      <div style='text-align:center'>
        <div style='font-size:22px;font-weight:700'>{esc(p.full_name) or 'Your Name'}</div>
        <div style='color:#6b7280;font-size:12px;margin-top:2px'>{esc(_contact_line(p))}</div>
      </div>
      {_section('Professional Summary', accent)}
      <div>{esc(p.summary)}</div>
      {_section('Education', accent)}{edu}
      {_section('Skills', accent)}
      <div><b>Skills:</b> {esc(skills)}</div>
      {_section('Work Experience', accent)}{exp}
    </div>"""


def _section(title: str, accent: str) -> str:
    return (f"<div style='font-weight:700;letter-spacing:0.5px;color:{accent};"
            f"border-bottom:1px solid #d9ddd6;margin:12px 0 6px;padding-bottom:2px'>"
            f"{title.upper()}</div>")


def _slug(text: str, limit: int = 40) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return out[:limit].strip("-") or "resume"


def resume_path(p: Profile) -> Path:
    ensure_dirs()
    stem = f"{_slug(p.full_name, 30)}-resume-{time.strftime('%Y-%m-%d')}"
    return resumes_dir() / f"{stem}.pdf"


def render_resume_pdf(p: Profile, path: Path, *, template: str = "Standard") -> Path | None:
    try:
        from reportlab.lib.colors import HexColor
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import (HRFlowable, ListFlowable, ListItem,
                                        Paragraph, SimpleDocTemplate, Spacer)
    except ImportError:
        return None

    accent = HexColor("#14432f")
    muted = HexColor("#5a5a5a")
    serif = "Times-Roman" if template == "Standard" else "Helvetica"
    serif_b = "Times-Bold" if template == "Standard" else "Helvetica-Bold"

    name_s = ParagraphStyle("name", fontName=serif_b, fontSize=20, leading=23,
                            alignment=TA_CENTER, spaceAfter=2)
    contact_s = ParagraphStyle("contact", fontName=serif, fontSize=9, leading=12,
                               alignment=TA_CENTER, textColor=muted, spaceAfter=6)
    sect_s = ParagraphStyle("sect", fontName=serif_b, fontSize=11, leading=13,
                            textColor=accent, spaceBefore=9, spaceAfter=2)
    body_s = ParagraphStyle("body", fontName=serif, fontSize=9.5, leading=13)
    role_s = ParagraphStyle("role", fontName=serif_b, fontSize=10, leading=13, spaceBefore=5)
    bullet_s = ParagraphStyle("bullet", parent=body_s, leftIndent=8)

    def esc(s: str) -> str:
        return html.escape(s or "")

    def sect(t: str):
        return [Paragraph(t.upper(), sect_s),
                HRFlowable(width="100%", thickness=0.6, color=HexColor("#bfc4bc"),
                           spaceBefore=1, spaceAfter=3)]

    flow: list = [Paragraph(esc(p.full_name) or "Your Name", name_s)]
    if _contact_line(p):
        flow.append(Paragraph(esc(_contact_line(p)), contact_s))
    if p.summary:
        flow += sect("Professional Summary"); flow.append(Paragraph(esc(p.summary), body_s))
    if p.education:
        flow += sect("Education")
        for e in p.education:
            flow.append(Paragraph(f"<b>{esc(e)}</b>", body_s))
    if p.skills:
        flow += sect("Skills")
        flow.append(Paragraph("<b>Skills:</b> " + esc(", ".join(p.skills)), body_s))
    if p.experience:
        flow += sect("Work Experience")
        for e in p.experience:
            period = f"{esc(e.start)} – {esc(e.end) or 'Present'}"
            flow.append(Paragraph(f"{esc(e.company)} &nbsp;|&nbsp; {esc(e.title)} "
                                  f"<font color='#5a5a5a'>({period})</font>", role_s))
            if e.highlights:
                flow.append(ListFlowable(
                    [ListItem(Paragraph(esc(h), bullet_s), leftIndent=10, value="circle")
                     for h in e.highlights],
                    bulletType="bullet", bulletFontSize=4, leftIndent=10, spaceBefore=1))

    try:
        doc = SimpleDocTemplate(str(path), pagesize=A4,
                                leftMargin=18 * mm, rightMargin=18 * mm,
                                topMargin=15 * mm, bottomMargin=15 * mm,
                                title=f"{p.full_name} resume", author=p.full_name)
        doc.build(flow)
        return path
    except Exception:
        return None
