"""Choosing where the model runs, with the cost said out loud.

This exists because of a measurement. One application costs roughly 320,000
input tokens — a browser agent re-reads the page on each of its ~25 steps —
so the choice of model is worth about a hundredfold in money:

    frontier model   $3.35 per application
    gemini flash     $0.034

Nobody can be expected to guess that, and the natural thing to do is paste
in whatever key you already have, which is how a healthy subscription
empties in an afternoon. So every option here is labelled with what it costs
and what you get free, and the free ones are listed first.

The same widget serves onboarding and Settings. Whatever is chosen goes
through :func:`sushi.providers.apply_preset`, which is the one seam a hosted
option would later plug into.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QComboBox, QHBoxLayout, QLineEdit, QVBoxLayout,
                             QWidget)

from .. import providers
from ..config import load_settings
from .components import FlowLayout, button, faint, muted


class ModelPicker(QWidget):
    """Pick a provider, paste a key, done."""

    changed = pyqtSignal()

    def __init__(self, compact: bool = False) -> None:
        super().__init__()
        self._preset = providers.recommended()

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        if not compact:
            v.addWidget(muted("Used for reading your CV, writing letters and "
                              "filling forms. Searching never needs one."))

        # --- the options, free ones first ---------------------------------
        chips = QWidget()
        chips.setObjectName("ProviderChips")
        chips.setStyleSheet("QWidget#ProviderChips{background:transparent;}")
        flow = FlowLayout(chips, spacing=6)
        self._chips: dict[str, object] = {}
        for preset in providers.PRESETS:
            # Marked on the chip itself. Which options cost money is the one
            # thing worth knowing before clicking, and a note underneath is
            # read after the decision rather than before it.
            text = f"{preset.name}  ·  free" if preset.is_free else preset.name
            b = button(text, "Chip",
                       lambda _=False, p=preset: self._choose(p))
            self._chips[preset.name] = b
            flow.addWidget(b)
        v.addWidget(chips)

        # One line under the chips, not three. What you get free is already
        # on the chip; what the machine can serve is said by the model box
        # and, when it cannot, by the status line. That leaves the cost,
        # which is the fact this widget exists to put in front of someone.
        self.cost = faint("")
        v.addWidget(self.cost)

        # --- the model, where there is a choice ----------------------------
        self.model_row = QWidget()
        self.model_row.setObjectName("ModelRow")
        self.model_row.setStyleSheet("QWidget#ModelRow{background:transparent;}")
        mrow = QHBoxLayout(self.model_row)
        mrow.setContentsMargins(0, 0, 0, 0)
        mrow.setSpacing(8)
        mrow.addWidget(faint("Model"), 0, Qt.AlignmentFlag.AlignVCenter)
        self.model_box = QComboBox()
        self.model_box.setEditable(True)
        self.model_box.setMinimumWidth(260)
        mrow.addWidget(self.model_box, 1)
        v.addWidget(self.model_row)

        # --- the key --------------------------------------------------------
        self.key_row = QWidget()
        self.key_row.setObjectName("KeyRow")
        self.key_row.setStyleSheet("QWidget#KeyRow{background:transparent;}")
        krow = QHBoxLayout(self.key_row)
        krow.setContentsMargins(0, 0, 0, 0)
        krow.setSpacing(8)
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("Paste your API key")
        self.key_edit.returnPressed.connect(self._save)
        krow.addWidget(self.key_edit, 1)
        self.get_btn = button("Get a key", "Chip", self._open_signup,
                              icon="external-link")
        krow.addWidget(self.get_btn)
        krow.addWidget(button("Use this", "Primary", self._save))
        # Takes the width the key field would have had, so "Use this" stays a
        # button on the keyless options instead of stretching the row. A
        # hidden widget is skipped by the layout, which is what makes
        # swapping one for the other enough.
        self.key_spacer = QWidget()
        self.key_spacer.setObjectName("KeySpacer")
        self.key_spacer.setStyleSheet("QWidget#KeySpacer{background:transparent;}")
        krow.addWidget(self.key_spacer, 1)
        v.addWidget(self.key_row)

        self.status = faint("")
        v.addWidget(self.status)

        self._choose(self._preset, announce=False)
        self.refresh()

    # --- state ---------------------------------------------------------------

    def _choose(self, preset, announce: bool = True) -> None:
        self._preset = preset
        for name, b in self._chips.items():
            b.setObjectName("ChipActive" if name == preset.name else "Chip")
            b.style().unpolish(b)
            b.style().polish(b)
        self.cost.setText(preset.cost_note)

        self.model_box.clear()
        runtime = providers.runtime_module(preset.runtime)
        # The row stays; the field inside it comes and goes. Hiding the whole
        # row took "Use this" with it, which left the keyless options — the
        # two this app recommends first — with no way to be chosen at all.
        self.key_edit.setVisible(preset.needs_key)
        self.key_spacer.setVisible(not preset.needs_key)
        self.get_btn.setVisible(
            bool(preset.signup) and not (runtime is not None and runtime.available()))
        if runtime is None:
            self.model_box.addItem(preset.model)
        else:
            installed = runtime.models()
            if installed:
                best = runtime.best_model(installed)
                self.model_box.addItems([best] + [m for m in installed if m != best])
            else:
                self.model_box.addItem(f"({preset.name} is not available here)")
        if announce:
            self.status.setText("")
        self._announce_runtime()

    def _announce_runtime(self) -> None:
        """Speak only when this machine cannot serve the keyless option.

        ``describe()`` is a paragraph, and when the runtime *is* installed it
        repeats the cost note above it word for word. The case worth three
        lines is the other one: picked it, haven't got it, here is why.
        """
        runtime = providers.runtime_module(self._preset.runtime)
        if runtime is not None and not runtime.available():
            self.status.setText(runtime.describe())

    def _open_signup(self) -> None:
        if not self._preset.signup:
            return
        QDesktopServices.openUrl(QUrl(self._preset.signup))
        if self._preset.needs_key:
            self.status.setText(
                f"Opened {self._preset.name}. Create a key there, then paste "
                f"it above.")
        else:
            # Nothing to paste — the install is the whole setup.
            self.status.setText(
                f"Opened {self._preset.name}. Install it and sign in, then "
                f"reopen Sushi and pick this again.")

    def _save(self) -> None:
        """Store the choice. The key goes to settings and nowhere else."""
        preset = self._preset
        model = self.model_box.currentText().strip()
        if model.startswith("("):
            model = ""
        key = self.key_edit.text().strip()

        runtime = providers.runtime_module(preset.runtime)
        if preset.needs_key and not key and not self._existing_key():
            self.status.setText("Paste a key first, or pick a free option.")
            return
        if runtime is not None and not runtime.available():
            self.status.setText(runtime.describe())
            return

        providers.apply_preset(preset, api_key=key, model=model)
        self.key_edit.clear()      # never leave it on screen
        self.changed.emit()
        self.refresh()
        self.status.setText(f"Using {preset.name}" + (f" · {model}" if model else "") + ".")

    @staticmethod
    def _existing_key() -> bool:
        from .. import storage
        return bool(storage.get_setting("api_key", "").strip())

    def refresh(self) -> None:
        """Say what is in force, without ever showing the key."""
        s = load_settings()
        if not s.llm_enabled:
            self.status.setText("No model configured yet.")
            return
        where = s.key_source or "settings"
        self.status.setText(f"Ready · {s.model} (key from {where}).")
