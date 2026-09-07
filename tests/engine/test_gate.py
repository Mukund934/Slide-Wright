"""The deterministic quality gate.

Two properties matter most and are tested hardest:

  · no false positives on a well-formed deck — a gate that cries wolf gets
    switched off, and then it protects nothing;
  · every error carries a repair instruction an authoring model can act on.
"""

from __future__ import annotations

import pytest

from slide_wright.gate import (
    DENSE_WORDS_PER_SLIDE,
    MIN_BODY_PT,
    MIN_REPORTABLE_EMU,
    Severity,
    check,
    contrast_ratio,
)
from slide_wright.inspect import (
    EMU_PER_INCH,
    DeckInfo,
    ShapeInfo,
    SlideInfo,
    TextRun,
    inspect,
)

W = int(13.333 * EMU_PER_INCH)
H = int(7.5 * EMU_PER_INCH)


def deck_with(*shapes: ShapeInfo, number: int = 1) -> DeckInfo:
    return DeckInfo(
        slide_width=W,
        slide_height=H,
        slides=[SlideInfo(number=number, part_name="ppt/slides/slide1.xml", shapes=list(shapes))],
    )


def box(x=1.0, y=1.0, cx=4.0, cy=1.0, text="Hello", size=18.0, name="Box", sid="1"):
    return ShapeInfo(
        id=sid, name=name, kind="shape",
        x=int(x * EMU_PER_INCH), y=int(y * EMU_PER_INCH),
        cx=int(cx * EMU_PER_INCH), cy=int(cy * EMU_PER_INCH),
        runs=[TextRun(text=text, size_pt=size)] if text else [],
    )


class TestNoFalsePositives:
    def test_well_formed_slide_produces_nothing(self):
        assert check(deck_with(box())).findings == []

    def test_the_corpus_deck_passes_cleanly(self, adversarial_deck):
        result = check(inspect(adversarial_deck))
        assert result.passed
        assert result.findings == [], f"unexpected findings: {result.render()}"

    def test_the_control_deck_passes_cleanly(self, minimal_deck):
        assert check(inspect(minimal_deck)).passed

    def test_shape_touching_the_edge_exactly_is_allowed(self):
        exact = box(x=0.0, cx=13.333)
        assert not [f for f in check(deck_with(exact)).findings if f.code.startswith("bounds")]


class TestBounds:
    """The failure the gate exists for: text leaves the slide and never returns."""

    def test_horizontal_overflow_is_an_error(self):
        result = check(deck_with(box(x=10.0, cx=6.0)))
        overflow = [f for f in result.findings if f.code == "bounds.horizontal"]
        assert len(overflow) == 1
        assert overflow[0].severity is Severity.ERROR
        assert not result.passed

    def test_overflow_reports_the_actual_distance(self):
        result = check(deck_with(box(x=10.0, cx=6.0)))  # right edge at 16.0in on 13.33in
        assert "2.67in past the right edge" in result.findings[0].message

    def test_overflow_repair_gives_a_usable_number(self):
        f = check(deck_with(box(x=10.0, cx=6.0))).findings[0]
        assert "reduce width to at most 3.33in" in f.repair

    def test_vertical_overflow_is_an_error(self):
        result = check(deck_with(box(y=7.0, cy=2.0)))
        assert [f for f in result.errors if f.code == "bounds.vertical"]

    def test_negative_position_is_an_error(self):
        shape = box()
        shape.x = -EMU_PER_INCH
        assert [f for f in check(deck_with(shape)).errors if f.code == "bounds.offcanvas"]


class TestTypography:
    def test_tiny_text_warns_but_does_not_block(self):
        result = check(deck_with(box(size=MIN_BODY_PT - 2)))
        findings = [f for f in result.findings if f.code == "type.too_small"]
        assert findings and findings[0].severity is Severity.WARNING
        assert result.passed, "unreadable text is a warning, not a delivery blocker"

    def test_acceptable_text_is_silent(self):
        assert not [f for f in check(deck_with(box(size=MIN_BODY_PT))).findings]

    def test_inconsistent_title_sizes_are_reported(self):
        slides = []
        for i, pt in enumerate((44.0, 32.0, 28.0), start=1):
            title = ShapeInfo(id=str(i), name="Title", kind="placeholder",
                              placeholder_type="title",
                              x=0, y=0, cx=EMU_PER_INCH, cy=EMU_PER_INCH,
                              runs=[TextRun(text=f"T{i}", size_pt=pt)])
            slides.append(SlideInfo(number=i, part_name=f"s{i}", shapes=[title]))
        result = check(DeckInfo(slide_width=W, slide_height=H, slides=slides))
        assert [f for f in result.findings if f.code == "consistency.title_size"]

    def test_two_title_sizes_is_tolerated(self):
        slides = []
        for i, pt in enumerate((44.0, 44.0, 32.0), start=1):
            title = ShapeInfo(id=str(i), name="Title", kind="placeholder",
                              placeholder_type="title",
                              x=0, y=0, cx=EMU_PER_INCH, cy=EMU_PER_INCH,
                              runs=[TextRun(text=f"T{i}", size_pt=pt)])
            slides.append(SlideInfo(number=i, part_name=f"s{i}", shapes=[title]))
        result = check(DeckInfo(slide_width=W, slide_height=H, slides=slides))
        assert not [f for f in result.findings if f.code == "consistency.title_size"]


class TestLayout:
    def test_significant_text_overlap_is_reported(self):
        a = box(x=1.0, y=1.0, cx=4.0, cy=2.0, name="A", sid="1")
        b = box(x=2.0, y=1.5, cx=4.0, cy=2.0, name="B", sid="2")
        assert [f for f in check(deck_with(a, b)).findings if f.code == "layout.overlap"]

    def test_adjacent_boxes_do_not_overlap(self):
        a = box(x=1.0, cx=3.0, name="A", sid="1")
        b = box(x=4.0, cx=3.0, name="B", sid="2")
        assert not [f for f in check(deck_with(a, b)).findings if f.code == "layout.overlap"]

    def test_shapes_without_text_are_not_overlap_candidates(self):
        # A background rectangle sits under everything by design.
        bg = box(x=0.0, y=0.0, cx=13.333, cy=7.5, text="", name="Background", sid="1")
        fg = box(x=1.0, y=1.0, name="Text", sid="2")
        assert not [f for f in check(deck_with(bg, fg)).findings if f.code == "layout.overlap"]

    def test_a_small_label_on_a_large_shape_is_placement_not_collision(self):
        """The 34-of-105 case, and the reason the denominator changed.

        Measured against the smaller box this was always 100%, because anything
        small placed anywhere inside anything large is entirely inside it. One
        slide of the NASA Bhutan deck produced four such findings, every one a
        0.6x0.3in scale label sitting on a 9.4x6.0in map — which is where a
        scale label goes.
        """
        chart = box(x=2.0, y=1.0, cx=9.4, cy=6.0, name="Map", sid="1")
        label = box(x=3.5, y=6.1, cx=0.6, cy=0.3, text="200", name="Scale", sid="2")
        assert not [
            f for f in check(deck_with(chart, label)).findings
            if f.code == "layout.overlap"
        ]

    def test_two_boxes_of_a_size_still_collide(self):
        """Narrow, or the check is switched off rather than corrected."""
        a = box(x=1.0, y=1.0, cx=4.0, cy=2.0, name="A", sid="1")
        b = box(x=2.0, y=1.5, cx=4.0, cy=2.0, name="B", sid="2")
        found = [f for f in check(deck_with(a, b)).findings if f.code == "layout.overlap"]
        assert found
        assert "larger box" in found[0].message

    def test_a_group_is_not_a_text_box(self):
        """Its text is its children's and its box is their union, so comparing
        one against a shape it contains compares a thing with part of itself."""
        group = box(x=2.0, y=1.0, cx=9.0, cy=5.0, name="Group 14", sid="1")
        group.kind = "group"
        inner = box(x=3.0, y=2.0, cx=4.0, cy=3.0, name="Label", sid="2")
        assert not [
            f for f in check(deck_with(group, inner)).findings
            if f.code == "layout.overlap"
        ]

    def test_the_finding_says_which_box_it_measured(self):
        """"100%" meant two different things depending on the denominator, and
        the message never said which."""
        a = box(x=1.0, y=1.0, cx=4.0, cy=2.0, name="A", sid="1")
        b = box(x=1.5, y=1.2, cx=4.0, cy=2.0, name="B", sid="2")
        found = [f for f in check(deck_with(a, b)).findings if f.code == "layout.overlap"]
        assert found and "of the larger box" in found[0].message


class TestContent:
    def test_dense_slide_is_reported(self):
        result = check(deck_with(box(text="word " * (DENSE_WORDS_PER_SLIDE + 10))))
        assert [f for f in result.findings if f.code == "content.dense"]

    def test_empty_slide_is_reported(self):
        result = check(DeckInfo(slide_width=W, slide_height=H,
                                slides=[SlideInfo(number=1, part_name="s1")]))
        assert [f for f in result.findings if f.code == "content.empty"]


class TestRepairBrief:
    """The gate must speak to the authoring model, not only to a human."""

    def test_brief_lists_errors_only(self):
        result = check(deck_with(box(x=10.0, cx=6.0), box(x=1.0, y=5.0, size=6.0, sid="2")))
        brief = result.repair_brief()
        assert "slide 1" in brief
        assert "past the right edge" in brief
        assert "below the" not in brief  # warnings are excluded

    def test_brief_is_empty_when_clean(self):
        assert check(deck_with(box())).repair_brief() == ""

    def test_render_states_pass_or_fail(self):
        assert "passed" in check(deck_with(box())).render()
        assert "FAILED" in check(deck_with(box(x=10.0, cx=6.0))).render()


class TestContrast:
    @pytest.mark.parametrize(
        "fg,bg,expected",
        [("000000", "FFFFFF", 21.0), ("FFFFFF", "FFFFFF", 1.0)],
    )
    def test_known_ratios(self, fg, bg, expected):
        assert round(contrast_ratio(fg, bg), 1) == expected

    def test_ratio_is_symmetric(self):
        assert contrast_ratio("123456", "ABCDEF") == contrast_ratio("ABCDEF", "123456")

    def test_malformed_colour_does_not_raise(self):
        assert contrast_ratio("zzz", "FFFFFF") > 0


class TestRealDeckFindings:
    """The gate must find genuine defects in decks nobody curated for it."""

    def test_finds_real_defects_in_a_real_deck(self):
        from pathlib import Path

        deck = Path(r"C:/Users/mukun/Downloads/Lecture 1 & 2 NEW.pptx")
        if not deck.is_file():
            pytest.skip("external corpus deck not present on this machine")
        result = check(inspect(deck))
        assert result.findings, "a 64-slide lecture deck should not be flawless"
        assert all(f.repair for f in result.errors), "every error needs a repair instruction"


class TestAFindingCanStateItsOwnMagnitude:
    """A finding that quantifies the problem as nothing is a false finding.

    Measured across 29 real decks before the floor: 250 findings, of which 13
    read "extends 0.00in past the right edge" or "starts 0.00in left of the
    canvas" — ERRORs whose own numbers said nothing was wrong. `audit.py`'s
    doctrine is that a false finding costs more than a missed one, because a
    user told something wrong about their own deck stops trusting every later
    finding. Nine of those thirteen were reasons to disbelieve the other 237.

    The floor is derived from the printed precision rather than chosen, so the
    two cannot drift: change the format to three decimals and `BOUNDS_DECIMALS`
    has to change with it, which is a visible edit.
    """

    def _deck(self, shape: ShapeInfo) -> DeckInfo:
        return DeckInfo(
            slide_width=W, slide_height=H,
            slides=[SlideInfo(number=1, part_name="s1", shapes=[shape])],
        )

    def _shape(self, **over) -> ShapeInfo:
        base = dict(id="1", name="Box", kind="shape", x=0, y=0,
                    cx=EMU_PER_INCH, cy=EMU_PER_INCH)
        base.update(over)
        return ShapeInfo(**base)

    def test_an_overflow_too_small_to_print_is_not_reported(self):
        """A hair over the right edge, and the message would say 0.00in."""
        sliver = int(MIN_REPORTABLE_EMU) - 1
        result = check(self._deck(self._shape(x=W - EMU_PER_INCH + sliver)))
        assert not [f for f in result.findings if f.code.startswith("bounds")]

    def test_an_overflow_it_can_print_still_is(self):
        """Narrow, or the check is disabled rather than bounded."""
        over = int(MIN_REPORTABLE_EMU) * 4
        result = check(self._deck(self._shape(x=W - EMU_PER_INCH + over)))
        assert [f for f in result.findings if f.code == "bounds.horizontal"]

    def test_the_same_holds_below_the_bottom_edge(self):
        sliver = int(MIN_REPORTABLE_EMU) - 1
        result = check(self._deck(self._shape(y=H - EMU_PER_INCH + sliver)))
        assert not [f for f in result.findings if f.code == "bounds.vertical"]

    def test_and_off_the_left_of_the_canvas(self):
        result = check(self._deck(self._shape(x=-(int(MIN_REPORTABLE_EMU) - 1))))
        assert not [f for f in result.findings if f.code == "bounds.offcanvas"]

    def test_a_shape_genuinely_off_the_canvas_is_still_an_error(self):
        result = check(self._deck(self._shape(x=-EMU_PER_INCH)))
        assert [f for f in result.findings if f.code == "bounds.offcanvas"]

    def test_no_bounds_finding_ever_prints_a_zero_magnitude(self):
        """The invariant, asserted directly rather than through the threshold.

        Swept across a range that straddles the floor, so a future change to
        either the floor or the format string that reintroduces "0.00in" fails
        here rather than on somebody's deck.
        """
        import re

        for offset in range(0, 20_000, 137):
            for shape in (self._shape(x=W - EMU_PER_INCH + offset),
                          self._shape(y=H - EMU_PER_INCH + offset),
                          self._shape(x=-offset)):
                for finding in check(self._deck(shape)).findings:
                    if not finding.code.startswith("bounds"):
                        continue
                    stated = re.findall(r"(\d+\.\d+)in", finding.message)
                    assert stated and all(float(v) > 0 for v in stated), (
                        f"{finding.code} says {finding.message!r}, which claims "
                        "a problem of no size"
                    )
