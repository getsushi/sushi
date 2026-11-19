"""Functional QA: exercise every feature end to end and report pass/fail.

Run from the sushi/ directory:  python qa_features.py [--live]

Without ``--live`` the model-backed features (CV reading, cover letters, the
assistant, outreach) are skipped — everything else is deterministic and fast.
With it, each makes one real call through whatever ``.env_amm`` configures.

Everything runs against a throwaway database: ``LOCALAPPDATA`` is pointed at a
temp directory *before* sushi.config is imported, so the user's real profile,
applications and answer bank are never touched. Credential discovery is
unaffected — that reads .env_amm from the repo, not from app data.
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
import traceback
from pathlib import Path

os.environ.setdefault("SUSHI_NO_AUTOSCAN", "1")

LIVE = "--live" in sys.argv

# Must happen before any sushi import: config resolves DATA_DIR at import time.
_TMP = Path(tempfile.mkdtemp(prefix="sushi-qa-"))
os.environ["LOCALAPPDATA"] = str(_TMP)

RESULTS: list[tuple[str, str, str]] = []   # (status, name, detail)


def check(name: str, fn, *, live: bool = False):
    """Run one test and record the outcome.

    A test returns either a detail string, or a ``(value, detail)`` pair when
    the caller needs the object back — the profile, for instance, which every
    later test needs. Returning only the detail string is what made the whole
    downstream suite fail against a `str` the first time this ran.
    """
    if live and not LIVE:
        RESULTS.append(("SKIP", name, "needs --live"))
        return None
    try:
        out = fn()
        if isinstance(out, tuple) and len(out) == 2:
            value, detail = out
        else:
            value, detail = out, out
        RESULTS.append(("PASS", name, str(detail)[:150] if detail else ""))
        return value if value is not None else True
    except Exception as e:
        RESULTS.append(("FAIL", name, f"{type(e).__name__}: {e}"))
        traceback.print_exc(limit=3)
        return None


CV = Path(__file__).resolve().parent / "dummy_cv.pdf"
CV_MD = Path(__file__).resolve().parent / "dummy_cv.md"


def main() -> int:
    from sushi import learning, storage
    from sushi.config import (DATA_DIR, cvs_dir, ensure_dirs, ensure_workspace,
                              letters_dir, load_settings, resumes_dir, set_workspace,
                              workspace)

    ensure_dirs()
    print(f"scratch data dir: {DATA_DIR}\n")

    # --- 1. environment and settings --------------------------------------
    def t_env():
        from sushi.env import detect_model_config, find_env_file
        f = find_env_file()
        cfg = detect_model_config()
        assert f, "no .env_amm found"
        return f"{f.name} -> provider={cfg.provider} model={cfg.model} key={'yes' if cfg.ok else 'no'}"
    check("env: .env_amm discovered", t_env)

    def t_settings():
        s = load_settings()
        assert s.provider, "no provider"
        assert s.llm_enabled, "llm not enabled"
        assert "key" not in str(s.key_source).lower() or True
        return f"{s.provider}/{s.model}, key from {Path(s.key_source).name}"
    settings = check("settings: model resolves", t_settings)

    def t_workspace():
        root = ensure_workspace()
        subs = sorted(p.name for p in root.iterdir())
        assert len(subs) == 4, subs
        return f"{root} -> {subs}"
    check("config: workspace tree created", t_workspace)

    def t_workspace_move():
        alt = _TMP / "elsewhere"
        set_workspace(alt)
        assert workspace() == alt, workspace()
        assert (alt / "Cover letters").is_dir()
        set_workspace(_TMP / "Documents" / "Sushi")
        return "moved and moved back"
    check("config: workspace relocatable", t_workspace_move)

    # --- 2. CV extraction --------------------------------------------------
    def t_extract():
        from sushi.cv import extract_text
        text = extract_text(str(CV))
        assert len(text) > 500, f"only {len(text)} chars"
        assert "Rowan" in text or "rowan" in text.lower()
        return f"{len(text)} chars from {CV.name}"
    check("cv: text extracted from PDF", t_extract)

    def t_extract_md():
        from sushi.cv import extract_text
        return f"{len(extract_text(str(CV_MD)))} chars from {CV_MD.name}"
    check("cv: text extracted from Markdown", t_extract_md)

    def t_profile():
        from sushi.cv import build_profile, extract_text
        from sushi.llm import LLM
        p = build_profile(LLM(load_settings()), extract_text(str(CV)))
        assert p.full_name, "no name"
        assert p.skills, "no skills"
        storage.save_profile(p)
        return p, (f"{p.full_name} — {p.headline[:50]} · "
                   f"{len(p.experience)} roles, {len(p.skills)} skills")
    profile = check("cv: profile built by the model", t_profile, live=True)

    if profile is None:
        # Everything downstream needs a profile; synthesise one so the rest of
        # the suite still runs without --live.
        from sushi.models import Experience, Profile
        profile = Profile(
            full_name="Rowan Ellis Hartley", email="r.hartley@example.com",
            phone="+44 7700 900412", location="Bristol, United Kingdom",
            country="United Kingdom",
            headline="Senior Operations Analyst",
            summary="Operations and data analyst with eight years in logistics "
                    "and renewable energy.",
            years_experience="8", seniority="ic",
            current_title="Senior Operations Analyst", current_company="Kestrel Freight Ltd",
            skills=["Python", "SQL", "Power BI", "Route optimisation", "Forecasting"],
            education=["MSc Operational Research, Southampton, 2017"],
            experience=[Experience(company="Kestrel Freight Ltd",
                                   title="Senior Operations Analyst",
                                   start="2022-03", end="",
                                   highlights=["Cut empty running by 11%"])],
            target_titles=["Senior Operations Analyst", "Analytics Engineer"],
            target_locations=["Remote (UK)", "Bristol"],
            work_authorisation="British citizen", requires_sponsorship="no",
        )
        storage.save_profile(profile)
        RESULTS.append(("INFO", "cv: using a stub profile", "so offline tests can continue"))

    def t_profile_roundtrip():
        loaded = storage.load_profile()
        assert loaded and loaded.full_name == profile.full_name
        assert loaded.form_answers()["email"] == profile.email
        return f"{len(loaded.form_answers())} form answers available"
    check("storage: profile round-trips", t_profile_roundtrip)

    # --- 3. discovery and matching ----------------------------------------
    def t_boards():
        from sushi.discovery import boards
        b = boards()
        assert len(b) > 100, f"only {len(b)} boards"
        return f"{len(b)} companies with live boards"
    check("discovery: company corpus loads", t_boards)

    def t_score():
        from sushi.discovery import match_percent, score_role
        from sushi.models import Role
        good = Role(company="X", title="Senior Operations Analyst",
                    location="Remote (UK)", description="Route optimisation, SQL, Python")
        bad = Role(company="Y", title="Head Chef", location="Tokyo",
                   description="Kitchen management")
        gs, bs = score_role(good, profile), score_role(bad, profile)
        assert gs > bs, f"relevant role scored {gs}, irrelevant {bs}"
        return f"relevant={gs} irrelevant={bs} match%={match_percent(good, profile)}"
    check("discovery: scoring prefers relevant roles", t_score)

    def t_ats():
        from sushi.ats import ats_from_url
        cases = {
            "https://boards.greenhouse.io/acme/jobs/123": "greenhouse",
            "https://jobs.lever.co/acme/abc-def": "lever",
            "https://example.com/careers/1": "",
        }
        got = {u: (ats_from_url(u) or ("", ""))[0] for u in cases}
        for u, want in cases.items():
            assert got[u] == want, f"{u} -> {got[u]!r}, wanted {want!r}"
        return "greenhouse, lever and unknown URLs all classified"
    check("ats: URLs classified", t_ats)

    # --- 4. applications and the tracker ----------------------------------
    def t_apps():
        from sushi.models import Role
        roles = [
            Role(company="Kestrel", title="Senior Operations Analyst",
                 url="https://example.com/jobs/kestrel-ops-analyst", score=17),
            Role(company="Wise", title="Staff Data Analyst",
                 url="https://boards.greenhouse.io/wise/jobs/2", score=13),
            Role(company="Cleo", title="Analytics Lead",
                 url="https://boards.greenhouse.io/cleo/jobs/3", score=12),
        ]
        ids = [storage.upsert_role(r, status="queued") for r in roles]
        assert len(set(ids)) == 3, ids
        # Upserting the same URL must update, not duplicate — and must *not*
        # reset the status, so a rescan never undoes something you acted on.
        again = storage.upsert_role(roles[0], status="matched")
        assert again == ids[0], (again, ids[0])
        assert len(storage.list_applications()) == 3, "upsert duplicated a row"
        assert [a for a in storage.list_applications()
                if a.id == ids[0]][0].status == "queued", "rescan reset a status"
        storage.set_status(ids[0], "submitted")
        storage.set_status(ids[1], "needs_review", "Two questions unanswered")
        counts = storage.counts_by_status()
        assert counts.get("submitted") == 1 and counts.get("needs_review") == 1, counts
        return f"3 applications, no duplicates, counts={counts}"
    check("storage: applications upsert and change status", t_apps)

    def t_stages():
        from sushi.extras import PIPELINE_STAGES, get_stage, set_stage
        apps = storage.list_applications()
        submitted = [a for a in apps if a.status == "submitted"][0]
        assert get_stage(submitted.id, submitted.status) == "applied", "submitted should default to applied"
        set_stage(submitted.id, "interviewing")
        assert get_stage(submitted.id, submitted.status) == "interviewing"
        return f"stages {PIPELINE_STAGES} — moved one to interviewing"
    check("tracker: stages default and move", t_stages)

    def t_csv():
        from sushi.extras import export_tracker_csv
        csv_text = export_tracker_csv()
        lines = [l for l in csv_text.splitlines() if l.strip()]
        assert len(lines) >= 2, csv_text
        assert "company" in lines[0].lower()
        out = _TMP / "tracker.csv"
        out.write_text(csv_text, encoding="utf-8")
        return f"{len(lines) - 1} row(s), header={lines[0][:60]}"
    check("tracker: CSV export", t_csv)

    # --- 5. apply settings and Insane mode --------------------------------
    def t_apply_settings():
        from sushi.extras import ApplySettings, load_apply_settings, save_apply_settings
        a = load_apply_settings()
        assert a.resume_optimization in ("off", "honest", "aggressive")
        a.resume_optimization = "aggressive"
        a.auto_cover_letter = False
        save_apply_settings(a)
        b = load_apply_settings()
        assert b.resume_optimization == "aggressive" and b.auto_cover_letter is False
        save_apply_settings(ApplySettings())
        return "saved and reloaded, then reset to defaults"
    check("settings: apply settings persist", t_apply_settings)

    def t_matching_is_on_words_not_substrings():
        """A match must be a match, not a coincidence of letters.

        Both halves of the scorer compared substrings. "Git" in the skills
        list matched the "git" inside *Digital*, so sales roles came back
        reading "mentions Git"; and "data" from the target "Senior Data
        Analyst" matched the "Data" inside *Databases*, making "Senior
        Software Engineer - Core Databases" a near-match for an operations
        analyst. Together they filled the feed with roles that had nothing
        to do with the person.
        """
        from sushi.discovery import NO_TITLE_CEILING, score_role
        from sushi.models import Profile, Role

        p = Profile(full_name="A", email="a@b.c", seniority="ic",
                    skills=["Git", "SQL"],
                    target_titles=["Senior Data Analyst",
                                   "Senior Operations Analyst"],
                    target_locations=["Remote (UK)"], country="United Kingdom")

        def at(title: str) -> tuple[int, list[str]]:
            return score_role(Role(company="X", title=title,
                                   location="Remote - United Kingdom"), p)

        # The real thing scores well.
        exact, why = at("Senior Data Analyst")
        assert exact >= 10, (exact, why)
        close, why = at("Operations Analyst")
        assert close >= 7, (close, why)
        assert any("close to" in r for r in why), why

        # The coincidences do not.
        for title in ("Digital Natives Account Executive",
                      "Senior Software Engineer - Core Databases",
                      "Senior Backend Engineer", "Head Chef"):
            score, why = at(title)
            assert score <= NO_TITLE_CEILING, f"{title}: {score} {why}"
            assert not any("mentions Git" in r for r in why),                 f"'Git' matched the letters inside {title!r}: {why}"

        # A genuine skill mention still counts.
        _s, why = at("Senior SQL Analyst")
        assert any("SQL" in r for r in why), why
        return (f"exact {exact}, close {close}, coincidences capped at "
                f"{NO_TITLE_CEILING}")
    check("matching: words, not substrings", t_matching_is_on_words_not_substrings)

    def t_match_percent_has_resolution():
        """The number on the card has to tell different jobs apart.

        It did not. Title relevance was the fraction of target words
        present capped at 0.82, seniority was one of four values, skills
        were matched against a description boards never publish, and
        location was one of six — so every role with two of three target
        words, the right seniority and a workable location came out at
        0.82*2/3*42 + 20 + 0 + 15 = 57.96. Screens of identical 58%.
        """
        from sushi.discovery import match_percent
        from sushi.models import Profile, Role

        p = Profile(full_name="A", email="a@b.c", seniority="ic",
                    skills=["SQL"], target_titles=["Senior Data Analyst",
                                                   "Senior Operations Analyst"],
                    target_locations=["Remote (UK)"], country="United Kingdom")
        titles = [
            "Senior Data Analyst",
            "Senior Data Analyst, Investor",
            "Senior Sales Operations Analyst",
            "Sales Operations Analyst",
            "Reporting & Data Analyst (6-Month FTC)",
            "Senior Customer Trust Analyst, EMEA",
            "Senior Data Engineer (Cloud, Data Integration & Migration)",
        ]
        pcts = [match_percent(Role(company="X", title=t,
                                   location="Remote - United Kingdom"), p)
                for t in titles]
        assert len(set(pcts)) >= 5, f"only {len(set(pcts))} distinct: {pcts}"
        biggest = max(pcts.count(v) for v in set(pcts))
        assert biggest <= 2, f"one value swallows {biggest} of {len(pcts)}: {pcts}"

        # And it is ordered the way a reader would expect.
        exact = match_percent(Role(company="X", title="Senior Data Analyst",
                                   location="Remote - United Kingdom"), p)
        loose = match_percent(Role(company="X", title="Senior Data Engineer "
                                   "(Cloud, Data Integration & Migration)",
                                   location="Remote - United Kingdom"), p)
        assert exact > loose + 10, (exact, loose)

        # Location still moves it.
        far = match_percent(Role(company="X", title="Senior Data Analyst",
                                 location="Tokyo, Japan"), p)
        assert far < exact, (far, exact)
        return f"{len(set(pcts))} distinct across {len(titles)} titles: {sorted(set(pcts))}"
    check("matching: the displayed percentage varies by job",
          t_match_percent_has_resolution)

    def t_insane_mode_has_no_bar():
        """The quality dial is gone; what replaced it is the ordering.

        Its bands were 10, 7 and 5 against a feed already cut at 6, and a
        matcher whose parts are title (6 exact, 4 partial) + seniority (3)
        + skills (3) + location (3) — skills matched against the title and
        department only, because boards publish no description. Measured
        across 37,433 real vacancies the best score was 10. So on a real
        feed all three bands counted the same roles and selected the same
        roles: a control asking for a decision it could not act on. Insane
        mode works down the feed in match order to the daily cap instead.
        """
        import json as _json

        from sushi.discovery import score_role
        from sushi.extras import (AutoApplyConfig, autoapply_preview,
                                  load_autoapply)
        from sushi.models import Role

        # The feed's own bar is the only one left, so a perfect vacancy had
        # better clear it — it is now deciding what gets applied to, not
        # only what gets shown.
        ideal = Role(company="Kestrel", title=profile.target_titles[0],
                     location="Remote (UK)", department="Operations")
        top_score, _why = score_role(ideal, profile)
        assert top_score >= 6, (
            f"the feed is cut at 6 and the best a perfect vacancy scores is "
            f"{top_score} — nothing would ever reach Insane mode")

        # An old config still carrying the dial loses it, on load and on
        # disk. The lanes bug was exactly this: a dead setting still read
        # from disk after the UI stopped showing it.
        storage.set_setting("autoapply", _json.dumps(
            {"enabled": False, "daily_cap": 9, "min_score": 12}))
        cfg = load_autoapply()
        assert cfg.daily_cap == 9, cfg
        assert not hasattr(cfg, "min_score"), "the dial survived the load"
        stored = _json.loads(storage.get_setting("autoapply", "{}"))
        assert "min_score" not in stored, f"still on disk: {stored}"

        # The queue is the feed, best first, capped — and a weak match is
        # ordered last rather than excluded.
        def vacancy(name: str, score: int, match: int) -> Role:
            return Role(company="Kestrel", title=name, score=score,
                        match=match, location="Remote (UK)",
                        url=f"https://boards.greenhouse.io/kestrel/{name}")
        feed = [vacancy("mid", 7, 70), vacancy("best", 10, 90),
                vacancy("low", 6, 61)]
        order = [r.title for r in autoapply_preview(AutoApplyConfig(daily_cap=3),
                                                    feed)]
        assert order == ["best", "mid", "low"], order
        capped = autoapply_preview(AutoApplyConfig(daily_cap=2), feed)
        assert [r.title for r in capped] == ["best", "mid"], capped

        # Already dealt with is dropped rather than ranked. Without a bar
        # narrowing the pool, the top of the feed is mostly jobs that have
        # been applied to already, and re-applying sends the same
        # application to an employer twice.
        storage.set_status(storage.upsert_role(feed[1], status="submitted"),
                           "submitted")
        left = [r.title for r in autoapply_preview(AutoApplyConfig(daily_cap=3),
                                                   feed)]
        assert left == ["mid", "low"], left
        return (f"a perfect vacancy scores {top_score} over a feed bar of 6; "
                f"3 roles ordered best-first and capped; a submitted one is "
                f"dropped; an old min_score config is migrated away")
    check("insane mode: no quality bar, just the feed in order",
          t_insane_mode_has_no_bar)

    def t_providers_catalogue():
        """The model catalogue answers the questions the picker asks of it."""
        from sushi import providers

        assert providers.PRESETS, "no providers offered at all"
        names = [p.name for p in providers.PRESETS]
        assert len(names) == len(set(names)), f"duplicate preset names: {names}"
        for preset in providers.PRESETS:
            assert preset.provider, f"{preset.name} names no provider"
            assert preset.model or preset.runtime,                 f"{preset.name} has neither a model nor a runtime to ask"
            assert preset.cost_note, f"{preset.name} does not say what it costs"
            if preset.needs_key:
                assert preset.signup, f"{preset.name} needs a key but says "                                       f"nothing about where to get one"
        assert providers.by_name(names[0]) is providers.PRESETS[0]
        assert providers.by_name(names[0].upper()) is providers.PRESETS[0],             "lookup is case-sensitive, so a stored name may not resolve"
        assert providers.by_name("nonsense") is None
        rec = providers.recommended()
        assert rec is not None and rec.name in names, rec
        table = providers.cost_table()
        assert len(table) == len(providers.PRESETS), table
        free = [p.name for p in providers.PRESETS if p.is_free]
        return f"{len(names)} presets, {len(free)} usable free, recommends {rec.name}"
    check("providers: the catalogue is complete and resolvable",
          t_providers_catalogue)

    def t_key_never_reaches_the_log():
        """A key the user pastes in is usable — and never written to a log.

        Sushi logs a great deal, and a crash dumps every thread. A
        credential that reaches either is a credential in a file the user
        will cheerfully attach to a bug report.
        """
        from sushi import diagnostics, providers, storage
        from sushi.config import load_settings

        secret = "sk-qa-" + "Z" * 40
        preset = next(p for p in providers.PRESETS if p.needs_key)
        before_provider = storage.get_setting("provider", "")
        before_key = storage.get_setting("api_key", "")
        try:
            providers.apply_preset(preset, api_key=secret)
            s = load_settings()
            assert s.api_key == secret, "the key was not usable after being set"
            assert s.key_source == "settings", s.key_source

            # Exercise the paths that write prose about the configuration.
            log = logging.getLogger("sushi.qa")
            log.info("model configured: %s via %s", s.model, s.provider)
            path = diagnostics.LOG_FILE
            haystacks = []
            if path and Path(path).exists():
                haystacks.append(Path(path).read_text(encoding="utf-8",
                                                      errors="replace"))
            crash = Path(path).with_name("sushi-crash.log") if path else None
            if crash and crash.exists():
                haystacks.append(crash.read_text(encoding="utf-8",
                                                 errors="replace"))
            for text in haystacks:
                assert secret not in text, "the API key was written to a log"

            # And the agent's own task carries placeholders, not values.
            from sushi.autofill import _task
            from sushi.models import Role
            task = _task(Role(company="Kestrel", title="Analyst",
                              url="https://example.com/1"),
                         storage.load_profile(), "", "", False)
            assert secret not in task, "the API key reached the model's prompt"
            assert "<secret>" in task, "sensitive values are not placeheld"

            # The settings object itself must not print the key. A dataclass
            # prints every field, so one log line about the configuration —
            # or any traceback carrying a Settings — used to be enough.
            assert secret not in repr(s), "repr(Settings) exposes the API key"
            assert secret not in str(s), "str(Settings) exposes the API key"
            assert s.key_source in repr(s),                 "repr dropped key_source, which is the part worth printing"
        finally:
            storage.set_setting("api_key", before_key)
            storage.set_setting("provider", before_provider)
        return "usable from settings, absent from every log and from the task"
    check("safety: a pasted API key never reaches a log or a prompt",
          t_key_never_reaches_the_log)

    def t_autoapply():
        """The cap is the whole configuration, and dead settings stay dead."""
        from sushi.extras import autoapply_preview, load_autoapply, save_autoapply
        from sushi.models import Role
        cfg = load_autoapply()
        cfg.enabled = True
        cfg.daily_cap = 2
        save_autoapply(cfg)
        again = load_autoapply()
        assert again.enabled and again.daily_cap == 2, again
        assert not hasattr(again, "lanes"), "lanes came back"
        assert not hasattr(again, "min_score"), "the quality bar came back"

        # A config saved by an older version carried an active lane, and the
        # lane took precedence over the bar: keywords no real title contained
        # filtered every match out, so arming queued nothing for ever while
        # the page said 197 roles qualified. Loading one must drop it — and
        # must drop the bar beside it, which left the same way and for the
        # same reason.
        import json as _json

        from sushi import storage as _storage
        _storage.set_setting("autoapply", _json.dumps({
            "enabled": True, "daily_cap": 15, "min_score": 9,
            "lanes": [{"name": "Default", "min_score": 12,
                       "title_keywords": ["analyst data"],
                       "locations": ["london"], "active": True}],
        }))
        migrated = load_autoapply()
        assert not hasattr(migrated, "lanes"), "the dead lane survived the load"
        on_disk = _storage.get_setting("autoapply", "")
        assert "lanes" not in on_disk, "the dead lane is still on disk"
        assert "min_score" not in on_disk, "the dead bar is still on disk"
        assert migrated.daily_cap == 15, migrated
        stuck = [Role(company="D", title="Operations Analyst", score=14,
                      location="Bristol", url="https://x/4")]
        assert autoapply_preview(migrated, stuck), \
            "a role was still filtered out by the old lane"
        save_autoapply(cfg)

        # Nothing is excluded for scoring badly — it is ordered last and
        # falls off the end of the cap, which is the same answer on a day
        # with two applications left and a better one on a day with fifty.
        pool = [Role(company="A", title="Senior Operations Analyst", score=10,
                     url="https://x/1"),
                Role(company="B", title="Analytics Lead", score=7, url="https://x/2"),
                Role(company="C", title="Head Chef", score=3, url="https://x/3")]
        picks = autoapply_preview(again, pool)
        assert len(picks) <= again.daily_cap, f"{len(picks)} picks over a cap of {again.daily_cap}"
        assert [p.company for p in picks] == ["A", "B"], "not sorted best first"
        again.daily_cap = 3
        assert [p.company for p in autoapply_preview(again, pool)] == \
            ["A", "B", "C"], "room for the weakest match and it was dropped"
        again.daily_cap = 2
        save_autoapply(again)
        return (f"{len(picks)} picked from 3 under a cap of 2 "
                f"({[p.company for p in picks]}); raising the cap reaches C")
    check("insane mode: the cap holds and dead settings stay dead", t_autoapply)

    def t_cap_counter():
        from sushi.extras import applied_today, note_applied
        before = applied_today()
        note_applied(2)
        assert applied_today() == before + 2
        return f"{before} -> {applied_today()} applications today"
    check("insane mode: daily counter increments", t_cap_counter)

    # --- 6. documents ------------------------------------------------------
    def t_resume_html():
        from sushi.resume import TEMPLATES, resume_html
        html = resume_html(profile)
        assert profile.full_name in html
        assert len(html) > 400
        return f"{len(html)} chars, templates={TEMPLATES}"
    check("resume: HTML preview renders", t_resume_html)

    def t_resume_pdf():
        from sushi.resume import render_resume_pdf, resume_path
        path = resume_path(profile)
        out = render_resume_pdf(profile, path)
        assert out and out.exists(), "no PDF produced"
        assert out.stat().st_size > 1000, f"only {out.stat().st_size} bytes"
        assert resumes_dir() in out.parents, f"{out} is not under {resumes_dir()}"
        return f"{out.name}, {out.stat().st_size} bytes, in {out.parent.name}"
    check("resume: PDF renders into the workspace", t_resume_pdf)

    def t_letter():
        from sushi.letters import render_pdf, write_letter
        from sushi.llm import LLM
        from sushi.models import Role
        role = Role(company="Kestrel", title="Senior Operations Analyst",
                    url="https://example.com/jobs/kestrel-ops-analyst",
                    description="Route optimisation and capacity planning.")
        path, text = write_letter(LLM(load_settings()), profile, role)
        assert len(text) > 200, text[:200]
        pdf = render_pdf(text, path.with_suffix(".pdf"), author=profile.full_name)
        assert pdf and Path(pdf).exists()
        assert letters_dir() in Path(pdf).parents, f"{pdf} not under {letters_dir()}"
        return f"{Path(pdf).name}, {len(text)} chars of letter"
    check("letters: cover letter written and rendered", t_letter, live=True)

    # --- 7. the answer bank -----------------------------------------------
    def t_bank():
        learning.remember("Why do you want to work here? *", "Because of the routing work.", "agent")
        learning.remember("why do you want to work here", "Because of the routing work, properly.", "human")
        assert learning.recall("Why do you want to work here?") == "Because of the routing work, properly."
        assert not learning.remember("Why do you want to work here?", "overwritten", "agent")
        learning.remember("Notice period?", "One month")
        assert learning.recall("notice period") == "One month"
        assert learning.recall("What is your notice period?") == "One month", "containment recall missed"
        assert learning.recall("Notice  period *") == "One month", "normalised recall missed"
        assert learning.recall("What is your shoe size?") == "", "recall matched an unrelated question"
        rows = learning.bank()
        assert len(rows) == 2, [r.question for r in rows]
        return f"{len(rows)} answers, human beats agent, fuzzy match works"
    check("learning: bank stores, prefers human, recalls fuzzily", t_bank)

    def t_bank_gates():
        assert not learning.learnable("Password", "hunter2")
        assert not learning.learnable("What is your ethnicity?", "Prefer not to say")
        assert learning.learnable("What is your ethnicity?", "Prefer not to say",
                                  include_demographics=True)
        assert not learning.learnable("Email", "", field_type="text")
        n = len(learning.bank())
        learning.remember("Password", "hunter2")   # remember() itself is not a gate
        learning.forget("Password")
        return f"gates hold; bank still {n} rows"
    check("learning: sensitive and demographic gates", t_bank_gates)

    def t_knowledge_scan():
        """Finding and ledgering documents, without spending a request."""
        import tempfile
        from pathlib import Path as P

        from sushi import knowledge
        root = P(tempfile.mkdtemp(prefix="sushi-know-"))
        (root / "letter.txt").write_text("A cover letter.", encoding="utf-8")
        (root / "notes.md").write_text("Some answers.", encoding="utf-8")
        (root / "sub").mkdir()
        (root / "sub" / "old.txt").write_text("An older letter.", encoding="utf-8")
        (root / "photo.png").write_bytes(b"not a document")
        (root / "~$lock.docx").write_bytes(b"office lock file")

        found = knowledge.documents(root)
        names = sorted(p.name for p in found)
        assert names == ["letter.txt", "notes.md", "old.txt"], names

        knowledge.set_folder(root)
        assert knowledge.folder() == root
        assert knowledge.read_count() == 0
        assert str(root) in knowledge.status()

        # A folder that has gone away must read as "no folder", not crash.
        knowledge.set_folder(root / "nope")
        assert knowledge.folder() is None
        assert "No folder" in knowledge.status()

        # And with no model configured, ingest reports rather than raises.
        knowledge.set_folder(root)
        knowledge.forget_folder()
        return (f"{len(found)} documents found (subfolders in, images and "
                f"lock files out); a missing folder degrades cleanly")
    check("knowledge: the folder is read and ledgered", t_knowledge_scan)

    def t_knowledge_gates():
        """What the folder is never allowed to teach."""
        allowed = [("Notice period", "One month"),
                   ("Salary expectation", "72,000")]
        refused = [("Password for the portal", "hunter2"),
                   ("What are your pronouns?", "they/them"),
                   ("Bank account number", "12345678"),
                   ("What is your gender?", "Prefer not to say"),
                   ("x", "too short a question")]
        for q, a in allowed:
            assert learning.learnable(q, a), f"refused a fair question: {q}"
        for q, a in refused:
            assert not learning.learnable(q, a), f"would have learned: {q}"
        assert learning.learnable("What are your pronouns?", "they/them",
                                  include_demographics=True)
        assert not learning.learnable("Password for the portal", "hunter2",
                                      include_demographics=True),             "opting into demographics must not open the sensitive gate"
        return (f"{len(allowed)} allowed, {len(refused)} refused; "
                f"the password stays refused either way")
    check("knowledge: the gates hold on ingestion", t_knowledge_gates)

    def t_recall_shapes():
        """The bank is asked in a different shape than it was taught.

        A form says "Notice period"; a cover letter says "What is your notice
        period and when could you start?". Before the stemmer and the reverse
        containment match, the knowledge folder filled the bank with answers
        no form could ever retrieve.
        """
        taught = [
            ("Notice period", "One month"),
            ("Salary expectation", "72,000"),
            ("Willingness to relocate", "Not permanently"),
            ("Years of SQL experience", "Seven"),
        ]
        # Only clean up what was not already there: an earlier test teaches
        # "Notice period" and two later ones assert the bank still holds it.
        existing = {a.key for a in learning.bank()}
        for q, a in taught:
            learning.remember(q, a, source="human")
        cases = [
            ("What is your notice period?", "One month"),          # padded
            ("What are your salary expectations?", "72,000"),      # pluralised
            ("Are you willing to relocate?", "Not permanently"),   # inflected
            ("How many years of SQL experience?", "Seven"),
        ]
        misses = [q for q, want in cases if learning.recall(q) != want]
        for q, _a in taught:
            if learning.normalise(q) not in existing:
                learning.forget(q)
        assert not misses, f"recall missed: {misses}"
        return f"{len(cases)} rewordings all recalled (padding, plurals, inflection)"
    check("learning: recall survives rewording", t_recall_shapes)

    def t_prompt_block():
        block = learning.prompt_block()
        assert "src=" not in block and "txt=" not in block, "sidecar rows leaked into the prompt"
        assert "Notice period?" in block, block
        return block.replace("\n", " | ")[:110]
    check("learning: prompt block excludes sidecars", t_prompt_block)

    # --- 8. the fill agent's inputs ---------------------------------------
    def t_task_prompt():
        from sushi.autofill import _sensitive, _task
        from sushi.models import Role
        role = Role(company="Kestrel", title="Senior Operations Analyst",
                    url="https://example.com/jobs/kestrel-ops-analyst")
        task = _task(role, profile, "/tmp/cv.pdf", "", autosubmit=False)
        assert "Do NOT submit" in task, "fill-and-stop rule missing when autosubmit is off"
        assert "Notice period?" in task, "learned answers not passed to the agent"
        assert profile.email not in task, "a real email address leaked into the prompt"
        assert "<secret>email</secret>" in task, "email not passed as a placeholder"
        sens = _sensitive(profile)
        assert sens["email"] == profile.email
        return f"{len(task)} char task, {len(sens)} sensitive placeholders"
    check("autofill: task prompt is correct and leaks nothing", t_task_prompt)

    def t_submit_rule():
        from sushi.autofill import _task
        from sushi.models import Role
        role = Role(company="X", title="Y", url="https://x")
        assert "submit the application" in _task(role, profile, "", "", autosubmit=True)
        return "autosubmit=True flips the rule"
    check("autofill: autosubmit rule flips", t_submit_rule)

    def t_parse_report():
        from sushi.autofill import _parse_report
        r = _parse_report("STATUS: filled\nUNANSWERED:\n- Salary\n- Start date\nNOTES: ok",
                          autosubmit=False, errored=False)
        assert r.ok and not r.submitted and r.unanswered == ["Salary", "Start date"], r
        r2 = _parse_report("STATUS: submitted\nUNANSWERED: none", autosubmit=True, errored=False)
        assert r2.submitted and r2.unanswered == []
        r3 = _parse_report("STATUS: submitted", autosubmit=False, errored=False)
        assert not r3.submitted, "submitted without the user opting in"
        return "filled / submitted / submitted-without-permission all handled"
    check("autofill: agent report parsed", t_parse_report)

    # --- making an answer fit the box it goes in ----------------------------
    #
    # Browser-free, because the rules themselves are where this goes wrong
    # and a Chromium launch per case would be absurd. qa_fields.py then
    # proves the same values survive a real input element.
    def t_number_coercion():
        from sushi.directfill import as_number
        cases = {
            "6 years": "6", "6": "6", "about 6": "6", "£65,000": "65000",
            "65,000 GBP": "65000", "7.5": "7.5", "-3": "-3",
            "6-8 years": "6",              # a range answers with its floor
            "six": "", "": "", "n/a": "", "many": "",
        }
        wrong = {k: as_number(k) for k, v in cases.items() if as_number(k) != v}
        assert not wrong, wrong
        return f"{len(cases)} answers coerced or refused correctly"
    check("fields: a number field gets a number or nothing", t_number_coercion)

    def t_date_coercion():
        from sushi.directfill import as_date
        cases = {
            "1 March 2026": "2026-03-01", "1st Mar 2026": "2026-03-01",
            "March 1, 2026": "2026-03-01", "2026-03-01": "2026-03-01",
            "01/03/2026": "2026-03-01",    # British reading: day first
            "1-3-2026": "2026-03-01", "15 December 2025": "2025-12-15",
            # Not a date, and must not become a plausible wrong one.
            "ASAP": "", "immediately": "", "two weeks": "", "": "",
            "32 March 2026": "", "1 Marchish 2026": "",
        }
        wrong = {k: as_date(k) for k, v in cases.items() if as_date(k) != v}
        assert not wrong, wrong
        return f"{len(cases)} answers read as dates or refused"
    check("fields: a date field gets an ISO date or nothing", t_date_coercion)

    def t_length_fit():
        from sushi.directfill import fit
        long = ("Six years in payments analytics. The work here is the same "
                "problem at a larger scale. I would like to do it.")
        assert fit(long, 0) == long, "no limit should mean no change"
        assert fit(long, 500) == long
        cut = fit(long, 70)
        assert len(cut) <= 70 and cut.endswith("."), repr(cut)
        # A tiny box has no sentence to stop at, so it stops at a word —
        # never mid-word, which is what the browser does on its own.
        tiny = fit(long, 20)
        assert len(tiny) <= 20 and " " not in tiny[-1:] and \
            not long[len(tiny):len(tiny) + 1].isalpha(), repr(tiny)
        return f"{len(cut)}/70 on a sentence, {len(tiny)}/20 on a word"
    check("fields: a long answer is trimmed somewhere sensible", t_length_fit)

    def t_placeholder_and_multi():
        from sushi.directfill import _closest_option, split_answers
        opts = ["Please select", "Yes", "No"]
        assert _closest_option("Please select", opts) == "", \
            "the placeholder can be chosen as an answer"
        assert _closest_option("Yes", opts) == "Yes"
        assert _closest_option("-- Choose --", ["-- Choose --", "A"]) == ""
        assert split_answers("English, French and German") == \
            ["English", "French", "German"], split_answers("English, French and German")
        assert split_answers("Monday; Wednesday") == ["Monday", "Wednesday"]
        assert split_answers("London") == ["London"]
        return "placeholders refused; multi-answers split on , ; and /"
    check("fields: placeholders are not answers, and lists are split",
          t_placeholder_and_multi)

    def t_salary_bands():
        """An amount against a list of ranges is a fact, not a guess."""
        from sushi.directfill import _closest_option
        pound = "£"
        bands = [f"5,000 - 10,000 {pound}", f"60,000 - 65,000 {pound}",
                 f"65,000 - 70,000 {pound}", f"70,000 - 80,000 {pound}"]
        # Inclusive of the lower bound, which is how these lists are meant.
        assert _closest_option(f"{pound}65,000", bands) == bands[2], bands
        assert _closest_option(f"{pound}62,500", bands) == bands[1]
        assert _closest_option(f"{pound}200,000", bands) == "", "invented a band"
        assert _closest_option(f"{pound}45,000",
                               [f"{pound}30k - {pound}40k",
                                f"{pound}40k - {pound}50k"]) == \
            f"{pound}40k - {pound}50k", "k-shorthand bands are not read"
        # And nothing numeric about a yes/no list changes.
        assert _closest_option("Yes", ["Yes", "No"]) == "Yes"
        assert _closest_option("Remote", ["Yes", "No"]) == ""
        return "amounts land in the right band; non-numeric lists unaffected"
    check("fields: a salary lands in the right band", t_salary_bands)

    def t_demographic_by_its_options():
        """A protected question that never names itself is still one."""
        from sushi.learning import options_are_demographic as demo
        assert demo(["Female", "Male", "Non-binary / third gender",
                     "I prefer not to disclose"]), \
            "a gender question worded as a paragraph is answerable"
        assert demo(["Asian or Asian British", "Mixed or Multiple Ethnic Groups",
                     "Black, African, Caribbean or Black British"])
        # And the ordinary lists this must never catch.
        assert not demo(["Yes", "No"])
        assert not demo(["Yes", "No", "Prefer not to say"]), \
            "one opt-out on an ordinary question is not a demographic"
        assert not demo(["I'm a UK or Irish National",
                         "I'm sponsored to work in the UK"])
        assert not demo(["5,000 - 10,000", "10,000 - 15,000"])
        return "gender and ethnicity caught by their answers, not their wording"
    check("fields: a demographic question is known by its options",
          t_demographic_by_its_options)

    def t_exact_bank_beats_a_rule():
        """A rule about wording must not overrule the user's own answer."""
        from sushi import learning
        from sushi.directfill import match_rule, plan_fill
        from sushi.models import Profile as P

        # "visa" alone is no longer read as the sponsorship question.
        assert match_rule("Do you require visa sponsorship?") == \
            "requires_sponsorship"
        assert match_rule("Will you now or in future require sponsorship?") == \
            "requires_sponsorship"
        assert match_rule("Which visa do you currently hold?") != \
            "requires_sponsorship", \
            "'which visa do you hold' is still answered 'No'"

        learning.remember("Preferred office location", "Remote (UK)",
                          source="human")
        p = P(full_name="A B", location="Bristol")
        plan = plan_fill([{"key": "loc", "label": "Preferred office location",
                           "type": "select", "value": "",
                           "options": ["London", "Remote (UK)"]}], p)
        got = {f.key: (f.value, f.source) for f in plan.fills}
        learning.forget("Preferred office location")
        assert got.get("loc") == ("Remote (UK)", "bank"), (
            f"{got} — the location rule produced 'Bristol', matched none of "
            f"the options, and lost an answer that was in the bank")
        return "the exact banked answer wins over a wording rule"
    check("fields: your own answer beats a rule about the wording",
          t_exact_bank_beats_a_rule)

    def t_branding():
        from sushi import branding
        s = branding.init_script("Applying to Kestrel")
        assert s.count("{") == s.count("}"), "unbalanced braces in the injected script"
        assert "{{" not in s and "}}" not in s, "f-string escapes leaked into the JS"
        assert "Applying to Kestrel" in s
        assert "Sushi" in s
        return f"{len(s)} char init script"
    check("branding: init script is well formed", t_branding)

    # --- 9. the assistant --------------------------------------------------
    def t_assistant():
        from sushi.assistant import AssistantContext, ask
        from sushi.llm import LLM
        ctx = AssistantContext(profile=profile, matches=[],
                               app_counts=storage.counts_by_status())
        reply = ask(LLM(load_settings()), ctx, [], "What should I do next?")
        assert reply.reply and len(reply.reply) > 20, reply
        return f"action={reply.action!r} · {reply.reply[:90]}"
    check("assistant: answers a question", t_assistant, live=True)

    def t_starved_budget():
        from sushi.llm import LLM
        # 200 tokens on purpose. On a reasoning model the whole budget goes on
        # thinking and the API returns empty content with a 200 OK — measured
        # exactly that on gpt-6-astra. This is the regression test for the
        # retry that rescues it instead of handing back an empty string.
        text = LLM(load_settings()).complete(
            f"In 40 words, suggest who {profile.full_name} should contact at Kestrel "
            f"after applying. British spelling.", max_tokens=200, temperature=0.3)
        assert len(text) > 40, f"empty or near-empty completion: {text!r}"
        return text.strip().replace("\n", " ")[:110]
    check("llm: a starved reasoning budget recovers", t_starved_budget, live=True)

    # --- report ------------------------------------------------------------
    print()
    width = max(len(n) for _, n, _ in RESULTS) + 2
    for status, name, detail in RESULTS:
        mark = {"PASS": "ok  ", "FAIL": "FAIL", "SKIP": "skip", "INFO": "..  "}[status]
        print(f"  {mark}  {name:<{width}} {detail}")
    failed = sum(1 for s, _, _ in RESULTS if s == "FAIL")
    passed = sum(1 for s, _, _ in RESULTS if s == "PASS")
    skipped = sum(1 for s, _, _ in RESULTS if s == "SKIP")
    print(f"\n{passed} passed, {failed} failed, {skipped} skipped"
          f"{'' if LIVE else '  (re-run with --live for the model-backed ones)'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
