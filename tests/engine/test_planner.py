"""Planning: the only place a model influences the deck.

Every test here is really one question — can a wrong model output reach a file?
The answer must be no, whether the model hallucinates a shape, invents an
operation, misreads a type, or returns something that is not JSON at all.
"""

from __future__ import annotations

import json

import pytest

from slide_wright.changeset import Op
from slide_wright.inspect import inspect
from slide_wright.llm.client import Budget, BudgetExceeded, StubProvider, Usage
from slide_wright.planner import plan, summarise


@pytest.fixture
def deck(adversarial_deck):
    return inspect(adversarial_deck)


@pytest.fixture
def table_id(deck):
    return next(s for s in deck.all_shapes() if s.kind == "table").id


def responding(payload) -> StubProvider:
    return StubProvider([json.dumps(payload)])


class TestSummary:
    def test_includes_ids_kinds_and_text(self, deck):
        text = summarise(deck)
        assert "slide 1:" in text
        assert "kind=table" in text and "kind=chart" in text
        assert "Trading comparables" in text

    def test_reports_table_dimensions(self, deck):
        assert "rows=4 cols=4" in summarise(deck)

    def test_truncates_long_text(self, deck):
        assert all(len(line) < 200 for line in summarise(deck, max_text=20).splitlines())

    def test_contains_no_xml(self, deck):
        assert "<" not in summarise(deck)


class TestAcceptsValidPlans:
    def test_accepts_a_well_formed_table_edit(self, deck, table_id):
        result = plan(deck, "update the multiple", provider=responding([
            {"op": "set_table_cell", "slide": 3, "target": f"{table_id}/r1/c1",
             "before": "9.4x", "after": "11.8x", "rationale": "revised comps"},
        ]))
        assert len(result.changeset.changes) == 1
        assert result.dropped == []
        assert result.changeset.changes[0].op is Op.SET_TABLE_CELL

    def test_accepts_a_text_edit_whose_before_matches(self, deck):
        title = deck.slide(1).shapes[0]
        result = plan(deck, "retitle", provider=responding([
            {"op": "set_text", "slide": 1, "target": title.id,
             "before": title.text, "after": "New Title"},
        ]))
        assert len(result.changeset.changes) == 1


class TestRejectsInvalidPlans:
    """Hallucinations must die here, not at apply time."""

    def test_rejects_a_nonexistent_shape(self, deck):
        result = plan(deck, "x", provider=responding([
            {"op": "set_text", "slide": 1, "target": "99999", "before": "a", "after": "b"},
        ]))
        assert result.changeset.changes == []
        assert "is not on slide" in result.dropped[0][1]

    def test_rejects_a_nonexistent_slide(self, deck):
        result = plan(deck, "x", provider=responding([
            {"op": "set_text", "slide": 99, "target": "2", "before": "a", "after": "b"},
        ]))
        assert "does not exist" in result.dropped[0][1]

    def test_rejects_an_unsupported_operation(self, deck):
        result = plan(deck, "x", provider=responding([
            {"op": "delete_shape", "slide": 1, "target": "2"},
        ]))
        assert "cannot be applied surgically" in result.dropped[0][1]

    def test_rejects_an_unknown_operation(self, deck):
        result = plan(deck, "x", provider=responding([
            {"op": "reticulate_splines", "slide": 1, "target": "2"},
        ]))
        assert "unknown op" in result.dropped[0][1]

    def test_rejects_a_cell_edit_on_a_non_table(self, deck):
        result = plan(deck, "x", provider=responding([
            {"op": "set_table_cell", "slide": 1, "target": "2/r0/c0",
             "before": "a", "after": "b"},
        ]))
        assert "is not a table" in result.dropped[0][1]

    def test_rejects_a_cell_target_without_coordinates(self, deck, table_id):
        result = plan(deck, "x", provider=responding([
            {"op": "set_table_cell", "slide": 3, "target": table_id,
             "before": "9.4x", "after": "11.8x"},
        ]))
        assert "r<row>/c<col>" in result.dropped[0][1]

    def test_rejects_text_that_is_not_actually_present(self, deck):
        """Guards against a model inventing the text it claims to be replacing."""
        title = deck.slide(1).shapes[0]
        result = plan(deck, "x", provider=responding([
            {"op": "set_text", "slide": 1, "target": title.id,
             "before": "text that was never on this slide", "after": "b"},
        ]))
        assert "not present in shape" in result.dropped[0][1]

    def test_rejects_a_non_integer_slide(self, deck):
        result = plan(deck, "x", provider=responding([
            {"op": "set_text", "slide": "three", "target": "2", "before": "a", "after": "b"},
        ]))
        assert "must be an integer" in result.dropped[0][1]

    def test_keeps_valid_changes_alongside_rejected_ones(self, deck, table_id):
        result = plan(deck, "x", provider=responding([
            {"op": "set_table_cell", "slide": 3, "target": f"{table_id}/r1/c1",
             "before": "9.4x", "after": "11.8x"},
            {"op": "delete_shape", "slide": 1, "target": "2"},
        ]))
        assert len(result.changeset.changes) == 1
        assert len(result.dropped) == 1


class TestMalformedModelOutput:
    def test_invalid_json_produces_no_changes(self, deck):
        result = plan(deck, "x", provider=StubProvider(["this is not json at all"]))
        assert result.changeset.changes == []
        assert "invalid JSON" in result.dropped[0][1]

    def test_json_that_is_not_a_list_is_rejected(self, deck):
        result = plan(deck, "x", provider=StubProvider(['{"op": "set_text"}']))
        assert "did not return a list" in result.dropped[0][1]

    def test_fenced_json_is_tolerated(self, deck, table_id):
        payload = json.dumps([{"op": "set_table_cell", "slide": 3,
                               "target": f"{table_id}/r1/c1",
                               "before": "9.4x", "after": "11.8x"}])
        result = plan(deck, "x", provider=StubProvider([f"```json\n{payload}\n```"]))
        assert len(result.changeset.changes) == 1

    def test_empty_response_produces_no_changes(self, deck):
        result = plan(deck, "x", provider=StubProvider(["[]"]))
        assert result.changeset.changes == []
        assert result.dropped == []


class TestProvenance:
    def test_a_stub_plan_is_marked_as_such(self, deck):
        """A stub result must never be mistaken for a real one."""
        assert plan(deck, "x", provider=StubProvider(["[]"])).is_stub

    def test_completion_usage_is_recorded(self, deck):
        result = plan(deck, "x", provider=StubProvider(["[]"]))
        assert result.completion.usage.output_tokens >= 0


class TestBudget:
    def test_usage_accumulates(self):
        assert (Usage(10, 20) + Usage(5, 5)).input_tokens == 15

    def test_cost_is_computed_from_rates(self):
        # 1M input at $5 and 1M output at $25.
        assert Usage(1_000_000, 1_000_000).cost_usd(5.0, 25.0) == pytest.approx(30.0)

    def test_call_ceiling_stops_the_job(self, deck):
        budget = Budget(max_calls=1)
        plan(deck, "x", provider=StubProvider(["[]"]), budget=budget)
        with pytest.raises(BudgetExceeded, match="model calls"):
            plan(deck, "x", provider=StubProvider(["[]"]), budget=budget)

    def test_token_ceiling_stops_the_job(self, deck):
        budget = Budget(max_output_tokens=1)
        with pytest.raises(BudgetExceeded, match="output tokens"):
            plan(deck, "x" * 4000, provider=StubProvider(["[]" + " " * 4000]), budget=budget)


@pytest.fixture
def text_target(deck):
    """A shape with text of its own, and the slide it is on."""
    for slide in deck.slides:
        for shape in slide.shapes:
            if shape.kind not in ("table", "chart") and shape.text.strip():
                return slide.number, shape
    pytest.skip("fixture has no editable text")


class TestAValueTheDeckCannotHold:
    """`after` was passed through untyped, and the applier stringified it.

    A model returning `{"value": 12}` where a cell wants a figure had that dict
    written into the slide as `{'value': 12}` — on the deck, in the file, with
    the change report calling the result verified because the slide was in the
    change set. A `None` was written as `None`.

    A non-numeric point size was the other half: `float("huge")` raised
    ValueError, which is not an ApplyError, so it escaped the caller's handler
    and reached the API as a 500 rather than as a refusal with a reason.
    """

    def _proposal(self, slide, shape, **over):
        return [{"op": "set_text", "slide": slide, "target": shape.id,
                 "before": shape.text[:6], **over}]

    @pytest.mark.parametrize("value", [{"nested": "payload"}, ["a", "b"], None, True])
    def test_a_structure_is_never_a_replacement(self, deck, text_target, value):
        slide, shape = text_target
        result = plan(deck, "x", provider=responding(
            self._proposal(slide, shape, after=value)))
        assert not result.changeset.changes
        assert result.dropped

    def test_a_number_still_is_one(self, deck, text_target):
        """Narrow: a model writing 42 where a cell wants "42" is not an error."""
        slide, shape = text_target
        result = plan(deck, "x", provider=responding(
            self._proposal(slide, shape, after=42)))
        assert len(result.changeset.changes) == 1

    def test_a_point_size_must_be_a_number(self, deck, text_target):
        slide, shape = text_target
        result = plan(deck, "x", provider=responding([
            {"op": "set_font_size", "slide": slide, "target": shape.id, "after": "huge"}]))
        assert not result.changeset.changes
        assert "point size" in result.dropped[0][1]

    def test_a_coordinate_must_be_a_number(self, deck, text_target):
        slide, shape = text_target
        result = plan(deck, "x", provider=responding([
            {"op": "move", "slide": slide, "target": shape.id, "after": "far away"}]))
        assert not result.changeset.changes

    def test_the_applier_refuses_them_too(self, adversarial_deck, tmp_path, text_target):
        """The planner is not the only way a change is built, and a guarantee
        that lives in one caller is one the next caller does not have."""
        from slide_wright.apply import apply_changes
        from slide_wright.changeset import Change, ChangeSet

        slide, shape = text_target
        cs = ChangeSet(deck=str(adversarial_deck))
        cs.add(Change(id="x", op=Op.SET_TEXT, slide=slide, target=shape.id,
                      before=shape.text[:6], after={"nested": "payload"}))
        cs.approve_all()
        result = apply_changes(adversarial_deck, cs, tmp_path / "out.pptx")
        assert result.failed
        assert "writes text" in result.failed[0][1]


class TestEveryChangeHasItsOwnId:
    """The review surface addresses a change by id.

    `approve("c1")` walks the list and approves every match, so two changes
    sharing one id means clicking Approve on the row you read also approves the
    row you did not — in a product whose whole claim is that a person decided.
    A model picks these ids and nothing stopped it repeating one.
    """

    def test_a_repeated_id_is_made_unique(self, deck, text_target):
        slide, shape = text_target
        result = plan(deck, "x", provider=responding([
            {"id": "c1", "op": "set_text", "slide": slide, "target": shape.id,
             "before": shape.text[:6], "after": "one"},
            {"id": "c1", "op": "set_text", "slide": slide, "target": shape.id,
             "before": shape.text[:6], "after": "two"},
        ]))
        ids = [c.id for c in result.changeset.changes]
        assert len(ids) == len(set(ids)) == 2

    def test_approving_one_does_not_approve_the_other(self, deck, text_target):
        slide, shape = text_target
        result = plan(deck, "x", provider=responding([
            {"id": "c1", "op": "set_text", "slide": slide, "target": shape.id,
             "before": shape.text[:6], "after": "one"},
            {"id": "c1", "op": "set_text", "slide": slide, "target": shape.id,
             "before": shape.text[:6], "after": "two"},
        ]))
        changeset = result.changeset
        changeset.approve(changeset.changes[0].id)
        assert len(changeset.approved) == 1
        assert changeset.approved[0].after == "one"


class TestACellOffTheTable:
    def test_the_refusal_says_the_table_is_smaller_than_that(
        self, adversarial_deck, tmp_path, deck, table_id
    ):
        """"does not hold 'None'" sent a reader looking for a value in a cell
        that does not exist."""
        from slide_wright.apply import apply_changes
        from slide_wright.changeset import Change, ChangeSet

        # By kind, not by id. Shape ids are per-slide in OOXML, so "the slide
        # with a shape whose id is 3" is a different slide from "the slide with
        # the table" more often than not.
        slide = next(s.number for s in deck.slides
                     if any(x.kind == "table" for x in s.shapes))
        cs = ChangeSet(deck=str(adversarial_deck))
        cs.add(Change(id="x", op=Op.SET_TABLE_CELL, slide=slide,
                      target=f"{table_id}/r99/c99", after="x"))
        cs.approve_all()
        result = apply_changes(adversarial_deck, cs, tmp_path / "out.pptx")
        assert result.failed
        assert "is off it" in result.failed[0][1]
