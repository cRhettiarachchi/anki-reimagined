# Copyright: Anki Software, LLC and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

import json
from bisect import bisect_left

from anki.cards import Card, CardId
from anki.collection import Collection, OpChanges
from anki.decks import DeckId
from anki.errors import NotFoundError
from aqt import AnkiQt
from aqt.browser.previewer import CardPreview
from aqt.operations import QueryOp
from aqt.qt import QTimer
from aqt.sound import av_player
from aqt.toolbar import BottomBar
from aqt.utils import tr


class ManualStudy(CardPreview):
    """Read cards in ID order, independent of the review queue."""

    def __init__(self, mw: AnkiQt, deck_id: DeckId) -> None:
        self.mw = mw
        self._web = mw.web
        self._deck_id = deck_id
        self._ids: list[CardId] = []
        self._index = 0
        self._card: Card | None = None
        self._deck_name = ""
        self._generation = 0
        self._refresh_pending = False

    def card(self) -> Card | None:
        return self._card

    def show(self) -> None:
        av_player.stop_and_clear_queue()
        self._setup_web_view()
        self.mw.setStateShortcuts(
            [
                ("Left", lambda: self.move(-1)),
                ("Right", lambda: self.move(1)),
                ("Space", self.flip),
                ("E", self.edit),
                ("R", self.replay),
                ("Escape", lambda: self.mw.moveToState("deckBrowser")),
            ]
        )
        button = self.mw.button
        controls = f"""
<div class="manual-study">
    <div class="manual-status" aria-live="polite">
        <strong id="deck-name"></strong><span id="position"></span>
    </div>
    <div class="manual-controls">
        {button("previous", tr.qt_misc_previous_card(), "←", id="previous", extra="disabled")}
        {button("flip", tr.studying_show_answer(), "Space", id="flip", extra="disabled")}
        {button("next", tr.qt_misc_next_card(), "→", id="next", extra="disabled")}
        {button("edit", tr.studying_edit(), "E", id="edit", extra="disabled")}
        {button("add", tr.actions_add_note(), id="add", extra="hidden")}
    </div>
</div>"""
        BottomBar(self.mw, self.mw.bottomWeb).draw(controls, self, self._command)
        self.mw.web.setFocus()
        self.refresh()

    def op_executed(self, changes: OpChanges) -> None:
        if (
            changes.card
            or changes.note_text
            or changes.note
            or changes.deck
            or changes.notetype
        ):
            if not self._refresh_pending:
                # Legacy add-ons such as AnkiConnect call requireReset() just
                # before their write. Read after their event handler returns.
                self._refresh_pending = True
                QTimer.singleShot(0, self._refresh_after_operation)

    def _refresh_after_operation(self) -> None:
        self._refresh_pending = False
        if (
            self.mw.state == "manualStudy"
            and self.mw.manual_study is self
            and self.mw.col
        ):
            self.refresh()

    def refresh(self) -> None:
        self._generation += 1
        generation = self._generation

        def load(col: Collection) -> tuple[str | None, list[CardId]]:
            name = col.decks.name_if_exists(self._deck_id)
            return name, sorted(
                col.decks.cids(self._deck_id, children=True)
            ) if name else []

        def loaded(result: tuple[str | None, list[CardId]]) -> None:
            if (
                self.mw.state != "manualStudy"
                or self.mw.manual_study is not self
                or self._generation != generation
                or not self.mw.col
            ):
                return
            name, ids = result
            if name is None:
                self.mw.moveToState("deckBrowser")
                return
            self._deck_name = name
            current = self._card.id if self._card else None
            self._ids = ids
            self._index = (
                min(bisect_left(ids, current), len(ids) - 1) if ids and current else 0
            )
            self._load_current()

        QueryOp(parent=self.mw, op=load, success=loaded).run_in_background()

    def _load_current(self) -> None:
        old = self._card
        if self._ids:
            try:
                self._card = self.mw.col.get_card(self._ids[self._index])
            except NotFoundError:
                # A deletion can land after the ID query or between two key presses.
                self.refresh()
                return
            if not old or old.id != self._card.id:
                self._state = "question"
            # A note can be edited twice in the same second.
            self._last_state = None
            self._display_card()
        else:
            self._card = None
            self._state = "question"
            av_player.stop_and_clear_queue()
            self._update_flag_and_mark_icons(None)
            self.mw.web.eval(
                f'_showQuestion({json.dumps(tr.qt_misc_deck_is_empty())}, "", "");'
            )
        self._update_controls()

    def _update_controls(self) -> None:
        count = len(self._ids)
        position = (
            tr.qt_misc_manual_position(count=self._index + 1, total=count)
            if count
            else ""
        )
        flip = (
            tr.studying_show_answer()
            if self._state == "question"
            else tr.qt_misc_show_question()
        )
        self.mw.bottomWeb.eval(f"""
(() => {{
    const el = id => document.getElementById(id);
    el("deck-name").textContent = {json.dumps(self._deck_name)};
    el("position").textContent = {json.dumps(position)};
    el("flip").textContent = {json.dumps(flip)};
    el("previous").disabled = {json.dumps(self._index == 0)};
    el("next").disabled = {json.dumps(self._index + 1 >= count)};
    for (const id of ["previous", "flip", "next", "edit"]) el(id).hidden = {json.dumps(not count)};
    el("flip").disabled = el("edit").disabled = {json.dumps(not count)};
    el("add").hidden = {json.dumps(bool(count))};
}})();
""")
        self.mw.bottomWeb.adjustHeightToFit()

    def move(self, offset: int) -> None:
        index = self._index + offset
        if 0 <= index < len(self._ids):
            self._index = index
            self._load_current()

    def flip(self) -> None:
        if self._card:
            self._state = "answer" if self._state == "question" else "question"
            self._load_current()

    def edit(self) -> None:
        if self._card:
            self.mw.onEditCurrent()

    def replay(self) -> None:
        if self._card:
            self._on_replay_audio()

    def _command(self, cmd: str) -> None:
        if self.mw.state != "manualStudy" or self.mw.manual_study is not self:
            return
        if cmd == "previous":
            self.move(-1)
        elif cmd == "next":
            self.move(1)
        elif cmd == "flip":
            self.flip()
        elif cmd == "edit":
            self.edit()
        elif cmd == "add":
            self.mw.onAddCard()
