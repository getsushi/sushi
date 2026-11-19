"""Drive the real Settings → Where to look pane and report what it offers.

Written because the country list kept coming back unfiltered in the running
app while the widget filtered correctly in isolation: the difference has to
be something the page does to the picker, so the page is what this drives.
"""
from __future__ import annotations

import sys
import time

from PyQt6.QtCore import QEventLoop
from PyQt6.QtWidgets import QApplication


def main() -> int:
    app = QApplication(sys.argv)

    def settle(ms: int = 250) -> None:
        end = time.time() + ms / 1000
        while time.time() < end:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)

    from sushi import storage
    from sushi.models import Profile
    from sushi.ui import theme
    from sushi.ui.fonts import DISPLAY, load_fonts

    theme.refresh_stylesheet(display=load_fonts().get("display", DISPLAY))
    from sushi.ui.main_window import MainWindow

    storage.save_profile(Profile(
        full_name="Rowan Ellis Hartley", email="r.hartley@example.com",
        seniority="ic", target_titles=["Operations Analyst"],
        target_locations=["Remote (UK)"]))
    storage.set_setting("onboarded", "1")

    w = MainWindow()
    w.resize(1440, 920)
    w.move(-7000, -7000)
    w.show()
    settle(600)
    w.navigate("Settings")
    settle(400)

    page = w._pages["Settings"]
    page._go("Where to look")
    picker = page.where_picker

    def offered() -> list[str]:
        return [picker.country_box.itemText(i)
                for i in range(1, picker.country_box.count())]

    ok = True
    for region, want in (("North America", ["United States", "Canada", "Mexico"]),
                         ("APAC", ["India", "Singapore", "Japan"]),
                         ("LATAM", ["Brazil", "Mexico", "Argentina"])):
        # Click the chip the way a user does, then let the save/refresh round
        # trip run — ``set_profile`` re-enters ``on_show``, which reloads the
        # picker, and that is the step isolation testing does not reproduce.
        picker._region_btns[region].click()
        settle(400)
        got = offered()
        good = got[:3] == want and len(got) < 20
        ok &= good
        print(f"  {'PASS' if good else 'FAIL'}  {region}: {len(got)} offered, "
              f"first three {got[:3]}")
        print(f"        stored {picker.values()} · "
              f"effective {picker.effective()} · {picker.summary.text()}")
        picker._region_btns[region].click()   # back off for the next one
        settle(200)

    # And the case that started it: a country inside the region.
    picker._region_btns["APAC"].click()
    settle(300)
    picker._add_country(picker.country_box.findText("Pakistan"))
    settle(400)
    got = (picker.values(), picker.effective())
    good = got == (["APAC", "Pakistan"], ["Pakistan"])
    ok &= good
    print(f"  {'PASS' if good else 'FAIL'}  APAC + Pakistan: stored {got[0]}, "
          f"effective {got[1]}")
    print(f"        profile now: {w.profile.target_locations}")

    print("ALL PASSED" if ok else "FAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
