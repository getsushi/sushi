"""QA harness: render every screen of the app to PNG for visual review.

Run from the sushi/ directory:  python qa_shots.py [width height]

Uses the real `windows` platform with the window parked off-screen, because
`QT_QPA_PLATFORM=offscreen` reports zero installed fonts on this machine and
renders every glyph as a tofu box. Seeds realistic matches so grids are not
empty, and restores the onboarding flag on exit so a normal launch is
unaffected.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QEventLoop
from PyQt6.QtWidgets import QApplication

os.environ.setdefault("SUSHI_NO_AUTOSCAN", "1")

OUT = Path(__file__).resolve().parent / "shots"
SIZE = (1440, 920)


def settle(app: QApplication, ms: int = 450) -> None:
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)


def fake_matches(n: int = 9):
    from sushi.models import Role

    seed = [
        ("Kestrel", "Senior Operations Analyst", "London (hybrid)", 17,
         "Route optimisation is the core of the role"),
        ("Deliveroo", "Lead Analytics Engineer", "Remote (UK)", 15,
         "Demand forecasting matches your last three years"),
        ("Octopus Energy", "Operations Data Scientist", "London", 14,
         "Decision-support tooling, same stack"),
        ("Wise", "Staff Data Analyst", "Remote (EU)", 13,
         "Payments ops, adjacent domain"),
        ("Cleo", "Analytics Lead", "London", 12,
         "Small team, broad ownership"),
        ("Zopa", "Senior Data Analyst", "Remote (UK)", 11,
         "Forecasting and capacity planning"),
        ("Starling Bank", "Operations Manager", "Southampton", 10,
         "People leadership, ops background"),
        ("Tide", "Business Intelligence Lead", "Remote", 9,
         "BI ownership end to end"),
        ("Freetrade", "Data Analyst", "London", 8,
         "Earlier stage than your current level"),
    ]
    return [
        Role(company=c, title=t, location=loc, score=s, match=0,
             url=f"https://boards.greenhouse.io/{c.lower().replace(' ', '')}/jobs/{i}",
             ats=["greenhouse", "ashby", "lever"][i % 3], reasons=[why],
             salary=["£70,000 - £85,000", "£90,000 - £110,000",
                     "£65,000 - £78,000", ""][i % 4],
             description=f"{t} at {c}. " + ("Lorem ipsum role description. " * 40))
        for i, (c, t, loc, s, why) in enumerate(seed[:n])
    ]


def main() -> int:
    global SIZE
    if len(sys.argv) == 3:
        SIZE = (int(sys.argv[1]), int(sys.argv[2]))
    tag = f"{SIZE[0]}x{SIZE[1]}"
    OUT.mkdir(exist_ok=True)

    app = QApplication(sys.argv)
    from sushi import storage
    from sushi.ui import theme
    from sushi.ui.fonts import DISPLAY, load_fonts

    theme.refresh_stylesheet(display=load_fonts().get("display", DISPLAY))
    theme.apply_palette(app)      # as main.py does, or popups shoot dark
    from sushi.ui.main_window import MainWindow

    was_onboarded = storage.get_setting("onboarded", "")
    storage.set_setting("onboarded", "")

    # The splash first — it is the literal first frame of the app, and the
    # one screen a shot run used to never see.
    from sushi.ui.splash import Splash
    sp = Splash()
    sp.move(-7000, -7000)
    sp.show()
    sp.step("Loading your profile")
    settle(app, 500)
    sp.grab().save(str(OUT / f"{tag}_00_splash.png"))
    print("   00_splash", flush=True)
    sp.close()

    w = MainWindow()
    w.resize(*SIZE)
    w.move(-7000, -7000)
    w.show()
    settle(app, 800)

    def shot(name: str) -> None:
        w.grab().save(str(OUT / f"{tag}_{name}.png"))
        print("  ", name, flush=True)

    # --- onboarding, every step --------------------------------------------
    # In the order the flow runs them. The model and CV steps are gates, so
    # `go_to` clamps to whichever is unmet — on a machine that is already set
    # up all five shots are reachable, and on a bare one the later shots are
    # the gate, which is itself the thing worth looking at.
    flow = w.root_stack.currentWidget()
    for i, name in enumerate(["model", "cv", "where", "knowledge", "door"]):
        flow.go_to(i)
        settle(app, 500)
        shot(f"0{i + 1}_onboarding_{name}")
    flow._done()
    settle(app, 400)

    w.set_matches(fake_matches())
    settle(app, 300)

    # Give the tracker and the answer bank something to draw, then put both
    # back as they were — QA must not leave state behind.
    from sushi import learning
    from sushi.extras import PIPELINE_STAGES, get_stage, set_stage

    prior_stage_map = storage.get_setting("pipeline_stages", "")
    apps = storage.list_applications()
    for i, a in enumerate(apps[:4]):
        set_stage(a.id, PIPELINE_STAGES[min(i, len(PIPELINE_STAGES) - 1)])
    demo_answers = [
        ("Why do you want to work here?", "Because the route-optimisation work is "
         "the same problem I spent three years on at Kestrel, at a scale I have "
         "not had access to before.", "human"),
        ("Notice period", "One month", "human"),
        ("Are you legally authorised to work in the UK?", "Yes — British citizen", "human"),
        ("Salary expectation", "£72,000", "agent"),
    ]
    for q, a_, src in demo_answers:
        learning.remember(q, a_, source=src)

    pages = ["Insane mode", "Manual apply", "Dashboard", "All jobs", "Tracker",
             "Profile", "Settings"]
    for i, name in enumerate(pages, 4):
        w.navigate(name)
        settle(app, 600)
        shot(f"{i:02d}_{name.replace(' ', '_').lower()}")

    # --- sub-states --------------------------------------------------------
    w.navigate("Profile")
    settle(app, 300)
    p = w._pages["Profile"]
    for sub in ["Cover Letter", "Profile Details"]:
        p._set_sub(sub)
        settle(app, 400)
        shot(f"09_profile_{sub.split()[0].lower()}")

    w.navigate("Settings")
    s = w._pages["Settings"]
    for sec in ["Apply settings", "Learning", "Model", "Files", "Companies", "Account"]:
        s._go(sec)
        settle(app, 350)
        shot(f"10_settings_{sec.split()[0].lower()}")

    w.navigate("Insane mode")
    settle(app, 400)
    ia = w._pages["Insane mode"]
    ia.arm_btn.setChecked(True)
    settle(app, 700)
    shot("11_insane_armed")
    # The run table, which replaced the preview: the same list, except that
    # it is the one being worked through rather than one that might be.
    ia._paint_rows([
        {"key": "a", "company": "Kestrel", "title": "Senior Operations Analyst",
         "score": 17, "state": "filling", "detail": "Attaching your CV…"},
        {"key": "b", "company": "Wise", "title": "Staff Data Analyst",
         "score": 15, "state": "needs_review", "detail": "2 question(s) need you."},
        {"key": "c", "company": "Cleo", "title": "Analytics Lead",
         "score": 14, "state": "queued", "detail": ""},
        {"key": "d", "company": "Zopa", "title": "Senior Data Analyst",
         "score": 13, "state": "queued", "detail": ""},
    ])
    settle(app, 300)
    shot("12_insane_running")
    ia.arm_btn.setChecked(False)
    settle(app, 300)

    w.navigate("Dashboard")
    settle(app, 500)
    w.sidebar.set_collapsed(True)
    settle(app, 600)
    shot("13_sidebar_collapsed")
    w._toggle_assistant()
    settle(app, 400)
    shot("14_assistant_hidden")
    w._toggle_assistant()
    w.sidebar.set_collapsed(False)
    settle(app, 500)

    # --- a dialog ----------------------------------------------------------
    from sushi.ui.main_window import RoleDialog
    d = RoleDialog(w, w.matches[0])
    d.resize(560, 460)
    d.move(-7000 + 200, -7000 + 200)
    d.show()
    settle(app, 500)
    d.grab().save(str(OUT / f"{tag}_15_role_dialog.png"))
    print("   15_role_dialog", flush=True)
    d.close()

    for q, _a, _s in demo_answers:
        learning.forget(q)
    storage.set_setting("pipeline_stages", prior_stage_map)
    storage.set_setting("onboarded", was_onboarded)
    w.hide()
    print("done ->", OUT.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
