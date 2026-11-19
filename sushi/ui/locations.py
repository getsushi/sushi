"""Where you want to work — asked once, on first run, and editable after.

Sushi inferred this from the CV and never asked. That is fine for "they live
in Bristol" and wrong for everything people actually mean: *I am in Bristol
but I will take anything in the UK*, *I want EMEA-wide remote*, *I am in
Lagos and happy with anything remote from anywhere*. None of those is in a
CV, and all of them decide whether the feed is useful.

Three questions, because they are genuinely three:

- **Regions** — the clusters people think in. EMEA, APAC, LATAM, Europe,
  North America, or Worldwide. One click to say "anywhere in this part of
  the world".
- **Countries** — the specific ones, added from a list rather than typed, so
  the stored value is a name the matcher can resolve rather than whatever
  spelling the user reached for. Offered only once a region is picked, and
  only the ones that region contains: it is a narrowing of that answer, and
  asking for both at once made the step read as two unrelated questions.
- **How you want to work** — remote, hybrid, on site, or no preference.
  Combined with the above this is what separates "remote, from anywhere"
  from "remote, but it has to be a German contract".

The country list was pulled for a day on the strength of its dropdown being
unreadable — black-on-black, which is what a popup looked like under Windows'
dark palette before ``theme.apply_palette`` pinned the light one. The control
was never the problem, so it is back. The free-text line for cities is not:
it took a spelling the matcher then had to guess at.

What comes out is a plain list of place names on the profile, the same shape
as before: ``["EMEA", "United Kingdom"]``. They are read back by
:func:`sushi.discovery.countries_in` like any other location string, so
nothing downstream has to know this widget exists.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QVBoxLayout, QWidget

from ..discovery import (COUNTRY_CHOICES, PLACE_LABELS, REGION_CHOICES,
                         WORKPLACE_WORDS, WORKPLACES, countries_within,
                         narrowed_places)
from .components import FlowLayout, button, faint, muted
from .starfield import transparent

#: "Worldwide" is a region in the UI and a modality in the matcher — it means
#: "I do not need the job to be in any particular country", which is exactly
#: what a remote-from-anywhere search is.
WORLDWIDE = "Worldwide"

#: Set once the user has answered this themselves. Re-reading a CV replaces
#: the profile wholesale, and the CV's guess at where someone can work must
#: not overwrite the answer they gave on first run.
LOCATIONS_CHOSEN = "locations_chosen"


class LocationPicker(QWidget):
    """Regions and how you want to work. Emits :attr:`changed`."""

    changed = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("LocationPicker")
        self.setStyleSheet("QWidget#LocationPicker{background:transparent;}")
        self._regions: set[str] = set()
        self._countries: list[str] = []
        self._workplace: set[str] = {"any"}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        root.addWidget(muted("Regions"))
        self._region_btns: dict[str, QWidget] = {}
        regions = transparent(QWidget())
        rflow = FlowLayout(regions, spacing=6)
        for _code, name in REGION_CHOICES:
            b = button(name, "Chip", lambda n=name: self._toggle_region(n))
            self._region_btns[name] = b
            rflow.addWidget(b)
        root.addWidget(regions)

        # Countries are a narrowing of the answer above, so there is nothing
        # to narrow until a region is picked. Shown as a whole — label and
        # all — rather than a disabled control, because a greyed-out box on
        # first run reads as something broken rather than something not yet
        # relevant.
        self._country_pick = transparent(QWidget())
        pick_col = QVBoxLayout(self._country_pick)
        pick_col.setContentsMargins(0, 0, 0, 0)
        pick_col.setSpacing(10)
        pick_col.addWidget(muted("Countries"))
        pick_row = QHBoxLayout()
        pick_row.setSpacing(8)
        self.country_box = QComboBox()
        self._offered: list[str] = []
        self.country_box.setFixedWidth(220)
        self.country_box.activated.connect(self._add_country)
        pick_row.addWidget(self.country_box)
        pick_row.addStretch(1)
        pick_col.addLayout(pick_row)
        root.addWidget(self._country_pick)

        self._chosen = transparent(QWidget())
        self._chosen_flow = FlowLayout(self._chosen, spacing=6)
        root.addWidget(self._chosen)

        root.addWidget(muted("How do you want to work?"))
        self._place_btns: dict[str, QWidget] = {}
        places = transparent(QWidget())
        pflow = FlowLayout(places, spacing=6)
        for key in WORKPLACES:
            b = button(WORKPLACE_WORDS[key].split(":")[0].strip(), "Chip",
                       lambda k=key: self._set_workplace(k))
            self._place_btns[key] = b
            pflow.addWidget(b)
        root.addWidget(places)

        self.summary = faint("")
        root.addWidget(self.summary)
        self._paint()

    # --- state ---------------------------------------------------------------

    def values(self) -> list[str]:
        """The places, as ``Profile.target_locations`` wants them."""
        ordered = [n for _c, n in REGION_CHOICES if n in self._regions]
        return list(dict.fromkeys(ordered + self._countries))

    def effective(self) -> list[str]:
        """:meth:`values`, as the matcher reads it — regions narrowed.

        The region stays on the profile so the chip stays lit and the country
        box stays filtered to it; it just stops widening the search once a
        country inside it has been named. See
        :func:`sushi.discovery.narrowed_places`.
        """
        places = self.values()
        kept = narrowed_places({PLACE_LABELS[p] for p in places
                                if p in PLACE_LABELS})
        return [p for p in places if PLACE_LABELS.get(p, p) in kept]

    def set_values(self, values: list[str]) -> None:
        """Load a profile's places back in, as far as this widget asks.

        Anything that is neither a region nor a country on the list — a city,
        a county, something typed into the free-text line this widget used to
        have — is read and dropped. There is nowhere on screen to show it any
        more, and a filter that narrows the feed with no control to clear it
        is worse than a setting quietly lost.
        """
        self._regions.clear()
        self._countries.clear()
        regions = {n for _c, n in REGION_CHOICES}
        for raw in values or []:
            name = (raw or "").strip()
            if not name:
                continue
            match = next((label for label in PLACE_LABELS
                          if label.lower() == name.lower()), "")
            if match in regions:
                self._regions.add(match)
            elif match and match not in self._countries:
                self._countries.append(match)
        self._paint()

    def workplace(self) -> str:
        """The chosen ways of working, as the profile stores them."""
        ordered = [k for k in WORKPLACES if k in self._workplace]
        return ",".join(ordered) or "any"

    def set_workplace(self, value: str | set[str]) -> None:
        picks = ({value} if isinstance(value, str) and value in WORKPLACES
                 else set(value) if not isinstance(value, str)
                 else {p for p in value.replace(",", " ").split() if p in WORKPLACES})
        self._workplace = picks or {"any"}
        if "any" in self._workplace:
            self._workplace = {"any"}
        self._paint()

    def is_set(self) -> bool:
        """Whether the user has actually chosen anything here."""
        return bool(self.values()) or self._workplace != {"any"}

    # --- interaction ---------------------------------------------------------

    def _toggle_region(self, name: str) -> None:
        if name in self._regions:
            self._regions.discard(name)
        else:
            # Worldwide and a specific region are contradictory answers to the
            # same question, so picking one puts the other down rather than
            # leaving a selection that cannot mean anything.
            if name == WORLDWIDE:
                self._regions.clear()
            else:
                self._regions.discard(WORLDWIDE)
            self._regions.add(name)
        self._paint()
        self._announce()

    def _add_country(self, index: int) -> None:
        if index <= 0:
            return
        name = self.country_box.itemText(index)
        if name not in self._countries:
            self._countries.append(name)
        self.country_box.setCurrentIndex(0)
        self._paint()
        self._announce()

    def _remove_country(self, name: str) -> None:
        if name in self._countries:
            self._countries.remove(name)
        self._paint()
        self._announce()

    def _set_workplace(self, key: str) -> None:
        """Toggle one way of working. Several at once is a real answer.

        "Remote or hybrid, but not five days in an office" is what most
        people mean, and a single-choice control could not say it.
        """
        if key == "any":
            self._workplace = {"any"}
        elif key in self._workplace:
            self._workplace.discard(key)
            if not self._workplace:
                self._workplace = {"any"}
        else:
            self._workplace.discard("any")
            self._workplace.add(key)
        self._paint()
        self._announce()

    def _announce(self) -> None:
        self._summarise()
        self.changed.emit()

    # --- painting ------------------------------------------------------------

    def _paint(self) -> None:
        for name, b in self._region_btns.items():
            self._chip_state(b, name in self._regions)
        for key, b in self._place_btns.items():
            self._chip_state(b, key in self._workplace)

        while self._chosen_flow.count():
            item = self._chosen_flow.takeAt(0)
            # Bound once: ``item.widget()`` re-resolves on every call and
            # returns None after the first has reparented it.
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for name in self._countries:
            b = button(f"{name}  ✕", "ChipActive",
                       lambda n=name: self._remove_country(n))
            b.setToolTip(f"Remove {name}")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self._chosen_flow.addWidget(b)
        self._chosen.setVisible(bool(self._countries))
        self._refresh_countries()
        # Countries already on the profile keep their picker, whether or not
        # a region came with them: hiding it would leave chips that can be
        # removed but not added back, and a saved profile can hold a country
        # without a region.
        self._country_pick.setVisible(bool(self._regions) or bool(self._countries))
        self._summarise()

    def _refresh_countries(self) -> None:
        """Offer the countries the chosen regions actually contain.

        The list used to be every country, whichever region was picked, so
        saying APAC and opening the box got United Kingdom, Ireland, Germany
        — a list that reads as the region having been ignored. Countries are
        a narrowing of the region, so the box narrows with it.

        No region to narrow by — nothing picked, Worldwide, or a saved
        profile that holds a country on its own — means the whole list, since
        there is no answer above for a country to contradict.
        """
        covered: set[str] = set()
        for name in self._regions:
            covered |= countries_within(PLACE_LABELS.get(name, ""))
        names = [name for code, name in COUNTRY_CHOICES
                 if not covered or code in covered]
        if names == self._offered:
            return
        self._offered = names
        # Rebuilt silently: ``activated`` only fires on a click, but clearing
        # the box moves the current index, and nothing downstream should read
        # that as the user having picked a country.
        blocked = self.country_box.blockSignals(True)
        self.country_box.clear()
        self.country_box.addItem("Add a country…")
        self.country_box.addItems(names)
        self.country_box.setCurrentIndex(0)
        self.country_box.blockSignals(blocked)

    @staticmethod
    def _chip_state(btn: QWidget, on: bool) -> None:
        btn.setObjectName("ChipActive" if on else "Chip")
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    def _summarise(self) -> None:
        # What the matcher will do, not what was clicked: a country narrows
        # the region above it, so saying "APAC · Pakistan" when the feed is
        # Pakistan would describe a filter the user does not have.
        places = self.effective()
        words = {"any": "any arrangement", "remote": "remote",
                 "hybrid": "hybrid", "onsite": "on site"}
        picked = [words[k] for k in WORKPLACES if k in self._workplace]
        work = " or ".join(picked) if picked else "any arrangement"
        if not places:
            self.summary.setText(
                "Nothing picked, so Sushi will go by where your CV says you "
                f"are. Showing {work}.")
            return
        if WORLDWIDE in places and self._workplace == {"remote"}:
            # The one combination worth naming, because it is the one people
            # mean when they say "remote" and do not get.
            self.summary.setText(
                "Remote from anywhere: roles open worldwide, not ones tied to "
                "a country you are not in.")
            return
        self.summary.setText(f"Showing roles in {' · '.join(places)}, {work}.")
