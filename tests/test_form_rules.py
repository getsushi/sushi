"""Form-filling rules that users have reported as bugs, kept from coming back."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sushi import ats, directfill, learning  # noqa: E402
from sushi.autofill import _wrong_match  # noqa: E402
from sushi.models import Profile  # noqa: E402

ME = Profile(full_name="Moazam Nadeem", email="m@example.com",
             location="Faisalabad, Punjab", country="Pakistan")


def _plan(fields):
    return directfill.plan_fill(fields, ME)


def test_gender_is_declined_not_asked():
    plan = _plan([{"key": "g", "label": "Gender", "type": "select",
                   "options": ["Select...", "Male", "Female",
                               "Decline to self-identify"]}])
    assert [(f.label, f.value) for f in plan.fills] == [
        ("Gender", "Decline to self-identify")]
    assert "Gender" not in plan.unanswered


def test_demographic_without_a_way_out_is_left_alone():
    plan = _plan([{"key": "v", "label": "Veteran status", "type": "select",
                   "options": ["I am a veteran", "I am not a veteran"]}])
    assert not plan.fills and not plan.unanswered


def test_decline_wordings():
    for o in ["Prefer not to say", "I don't wish to answer",
              "I do not want to answer", "Decline to self-identify"]:
        assert directfill.decline_option(["Yes", "No", o]) == o
    assert directfill.decline_option(["Male", "Female"]) == ""


def test_typeahead_wrong_match_is_caught():
    assert _wrong_match("University of Agriculture, Faisalabad",
                        "Tokyo University of Agriculture and Technology")
    # Legitimate shortened displays still pass.
    assert not _wrong_match("Pakistan", "+92")
    assert not _wrong_match("United Kingdom", "United Kingdom +44")
    assert not _wrong_match("Bachelor's Degree", "Bachelor's")


def test_options_and_placeholders_are_not_questions():
    for junk in ["Yes", "Custom", "Type your response", "Write here...",
                 "cards[abc][field0]", "Select..."]:
        assert learning.is_junk_question(junk), junk
    for real in ["Phone", "How did you hear about us?",
                 "Are you willing to relocate?"]:
        assert not learning.is_junk_question(real), real


def test_board_posting_dates_are_read():
    assert ats._when("2026-10-02T14:03:11-04:00") > 0
    assert ats._when(1_700_000_000_000) == 1_700_000_000.0   # Lever ms
    assert ats._when("") == 0.0 and ats._when("garbage") == 0.0
    assert abs(ats._when(time.time()) - time.time()) < 5


def test_careers_page_embedding_the_greenhouse_list_goes_to_this_jobs_form():
    from sushi.autofill import _GH_BOARD_EMBED, greenhouse_form_for
    board = "https://job-boards.greenhouse.io/embed/job_board?for=airbnb&validityToken=x"
    assert _GH_BOARD_EMBED.search(board)
    assert not _GH_BOARD_EMBED.search(
        "https://job-boards.greenhouse.io/embed/job_app?for=airbnb&token=1")
    assert greenhouse_form_for(board, "https://careers.airbnb.com/positions/8394382/") == (
        "https://job-boards.greenhouse.io/embed/job_app?for=airbnb&token=8394382")
    assert greenhouse_form_for(board, "https://x.com/jobs?gh_jid=1234567").endswith("token=1234567")
    # No job id: no form to go to, and the list is not followed.
    assert greenhouse_form_for(board, "https://careers.airbnb.com/") == ""


def test_profile_beats_an_old_saved_answer_for_identity(monkeypatch):
    monkeypatch.setattr(learning, "recall_exact",
                        lambda q: {"First Name": "M.", "Email": "old@example.com"}.get(q, ""))
    plan = _plan([{"key": "fn", "label": "First Name", "type": "text"},
                  {"key": "em", "label": "Email", "type": "email"}])
    got = {f.label: f.value for f in plan.fills}
    assert got == {"First Name": "Moazam", "Email": "m@example.com"}


def test_pasted_link_titles_and_company_are_clean():
    from sushi.extras import _clean_title, _company_from_host
    assert _clean_title("Engineering Manager, Search &amp; AI - Careers at Airbnb") == (
        "Engineering Manager, Search & AI", "Airbnb")
    assert _clean_title("Senior Engineer | Acme Careers") == ("Senior Engineer", "Acme")
    assert _company_from_host("https://careers.airbnb.com/positions/1/") == "Airbnb"
    assert _company_from_host("https://jobs.acme-corp.co.uk/x") == "Acme Corp"


def test_cli_agents_are_offered_and_routed():
    from sushi import providers
    from sushi.cli_agents import CODEX, OPENCODE, _opencode_parse, runtime_for
    names = [p.name for p in providers.PRESETS]
    assert {"Claude Code", "Codex", "OpenCode"} <= set(names)
    assert runtime_for("codex") is CODEX and runtime_for("opencode") is OPENCODE
    assert CODEX.INSTALL.startswith("npm install") and OPENCODE.LOGIN

    class P:
        stdout = ('{"type":"step_start"}\n'
                  '{"type":"text","part":{"text":"Hello"}}\n'
                  '{"type":"text","part":{"text":" there"}}\n')
    assert _opencode_parse(P(), "") == "Hello there"
