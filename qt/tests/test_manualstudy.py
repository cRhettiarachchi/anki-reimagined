# Copyright: Anki Software, LLC and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from anki.collection import Collection, OpChanges
from aqt.manualstudy import ManualStudy
from aqt.operations import QueryOp


def test_manual_study_revisits_edited_future_cards_without_grading(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    col = Collection(str(tmp_path / "collection.anki2"))
    try:
        parent = col.decks.id("Words")
        child = col.decks.id("Words::Places")

        def add(front, deck):
            note = col.new_note(col.models.current())
            note.fields[0] = front
            col.add_note(note, deck)
            return note.card_ids()[0]

        first = add("Island", child)
        second = add("Coast", parent)
        col.sched.set_due_date([first, second], "12!")
        col.sched.suspend_cards([second])

        def scheduling():
            return (
                col.db.all("select * from cards order by id"),
                col.db.all("select * from revlog order by id"),
            )

        before = scheduling()
        assert before[1]
        mw = MagicMock()
        mw.col = col
        mw.state = "manualStudy"
        study = mw.manual_study = ManualStudy(mw, parent)
        # Exercise the collection reads synchronously, leaving the Qt operation
        # notification on the real event loop.
        monkeypatch.setattr(
            QueryOp, "run_in_background", lambda op: op._success(op._op(col))
        )
        monkeypatch.setattr(study, "_display_card", lambda: None)
        monkeypatch.setattr(study, "_update_controls", lambda: None)
        study.refresh()
        study.move(-1)
        assert study.card().id == first
        study.flip()
        assert study.state() == "answer"
        study.move(1)
        study.move(1)
        assert study.card().id == second
        assert study.state() == "question"

        note = study.card().note()
        note.fields[1] = "Shoreline"
        study.op_executed(col.update_note(note))
        qapp.processEvents()
        study.move(-1)
        study.move(1)
        study.flip()
        assert study.card().id == second
        assert "Shoreline" in study.card().answer()
        assert scheduling() == before

        # AnkiConnect announces changes before writing in the same Qt event.
        study.op_executed(OpChanges(note=True))
        third = add("Hill", child)
        qapp.processEvents()
        assert study.card().id == second
        study.move(1)
        assert study.card().id == third

        col.remove_notes([study.card().nid])
        study.refresh()
        assert study.card().id == second
    finally:
        col.close()
