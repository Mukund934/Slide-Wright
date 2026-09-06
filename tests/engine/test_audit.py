"""Deck audit.

The audit is the first thing a new user runs, so a false finding costs more
than a missed one: a user who is told something wrong about their own deck
stops trusting every later finding. Precision is tested harder than recall.
"""

from __future__ import annotations

from slide_wright.audit import Area, audit
from slide_wright.gate import Severity
from slide_wright.inspect import (
    EMU_PER_INCH,
    DeckInfo,
    ShapeInfo,
    SlideInfo,
    TextRun,
    inspect,
)

W, H = int(13.333 * EMU_PER_INCH), int(7.5 * EMU_PER_INCH)


def shape(text="Body text", *, kind="shape", ph=None, size=18.0,
          font=None, colour=None, sid="1", cols=0):
    return ShapeInfo(
        id=sid, name=f"Shape{sid}", kind=kind, placeholder_type=ph,
        x=EMU_PER_INCH, y=EMU_PER_INCH, cx=4 * EMU_PER_INCH, cy=EMU_PER_INCH,
        table_cols=cols,
        runs=[TextRun(text=text, size_pt=size, font=font, color=colour)] if text else [],
    )


def titled(number: int, title: str, *extra: ShapeInfo) -> SlideInfo:
    head = shape(title, ph="title", sid=f"{number}t")
    return SlideInfo(number=number, part_name=f"s{number}", shapes=[head, *extra])


def deck(*slides: SlideInfo) -> DeckInfo:
    return DeckInfo(slide_width=W, slide_height=H, slides=list(slides))


class TestNoFalsePositives:
    def test_a_clean_deck_produces_no_observations(self):
        result = audit(deck(
            titled(1, "Revenue grew 38 percent", shape("ARR reached 4.2m USD")),
            titled(2, "Margins improved to 22 percent", shape("Gross margin 22%")),
            titled(3, "Churn fell to 1.9 percent", shape("Net churn 1.9%")),
        ))
        assert result.observations == [], result.render()

    def test_the_corpus_deck_is_broadly_clean(self, adversarial_deck):
        result = audit(inspect(adversarial_deck), "corpus")
        # It legitimately lacks source lines on its data slides; nothing else.
        areas = {o.area for o in result.observations}
        assert areas <= {Area.EVIDENCE}, result.render()

    def test_two_typefaces_is_not_sprawl(self):
        result = audit(deck(
            titled(1, "One", shape("a", font="Calibri")),
            titled(2, "Two", shape("b", font="Georgia")),
        ))
        assert not result.by_area(Area.CONSISTENCY)


class TestNarrative:
    def test_missing_titles_are_reported(self):
        untitled = SlideInfo(number=2, part_name="s2", shapes=[shape("orphan body")])
        result = audit(deck(titled(1, "Has a title"), untitled))
        found = result.by_area(Area.NARRATIVE)
        assert found and found[0].slides == [2]

    def test_duplicate_titles_are_reported_with_all_slides(self):
        result = audit(deck(
            titled(1, "Overview of results"),
            titled(2, "Overview of results"),
            titled(3, "Something else"),
        ))
        dupes = [o for o in result.by_area(Area.NARRATIVE) if "share the title" in o.message]
        assert dupes and dupes[0].slides == [1, 2]

    def test_weak_titles_are_reported_only_when_habitual(self):
        one_weak = audit(deck(titled(1, "Overview"), titled(2, "Revenue grew 38 percent")))
        assert not [o for o in one_weak.observations if "name a topic" in o.message]

        habitual = audit(deck(
            titled(1, "Overview"), titled(2, "Background"),
            titled(3, "Analysis"), titled(4, "Conclusion"),
        ))
        assert [o for o in habitual.observations if "name a topic" in o.message]


class TestEvidence:
    def test_data_slide_without_a_source_is_reported(self):
        result = audit(deck(titled(1, "Comps", shape("", kind="table", sid="t"))))
        found = result.by_area(Area.EVIDENCE)
        assert any("no source line" in o.message for o in found)

    def test_a_source_line_satisfies_the_check(self):
        result = audit(deck(titled(
            1, "Comps",
            shape("", kind="table", sid="t"),
            shape("Source: internal MIS, June 2026", sid="src"),
        )))
        assert not any("no source line" in o.message for o in result.by_area(Area.EVIDENCE))

    def test_bare_numbers_are_reported_when_habitual(self):
        slides = [titled(n, f"Slide {n}", shape(f"We processed {n * 1000} items"))
                  for n in range(1, 5)]
        result = audit(deck(*slides))
        assert any("no unit or currency" in o.message for o in result.observations)

    def test_numbers_with_units_are_not_reported(self):
        slides = [titled(n, f"Slide {n}", shape(f"Revenue of {n}.2m USD and 14% growth"))
                  for n in range(1, 5)]
        result = audit(deck(*slides))
        assert not any("no unit or currency" in o.message for o in result.observations)

    def test_percentages_count_as_units(self):
        slides = [titled(n, f"S{n}", shape(f"Growth of {n}4%")) for n in range(1, 5)]
        assert not any("no unit" in o.message for o in audit(deck(*slides)).observations)


class TestConsistency:
    def test_font_sprawl_is_reported(self):
        slides = [titled(n, f"S{n}", shape("x", font=f"Font{n}")) for n in range(1, 6)]
        found = audit(deck(*slides)).by_area(Area.CONSISTENCY)
        assert any("typefaces" in o.message for o in found)

    def test_colour_sprawl_is_reported(self):
        slides = [titled(n, f"S{n}", shape("x", colour=f"{n}{n}{n}{n}{n}{n}"))
                  for n in range(1, 9)]
        found = audit(deck(*slides)).by_area(Area.CONSISTENCY)
        assert any("colours" in o.message for o in found)


class TestLayout:
    def test_density_outlier_is_reported(self):
        normal = [titled(n, f"S{n}", shape("word " * 20)) for n in range(1, 5)]
        heavy = titled(5, "Dense", shape("word " * 300))
        found = audit(deck(*normal, heavy)).by_area(Area.LAYOUT)
        assert any(o.slides == [5] for o in found)

    def test_wide_table_is_reported(self):
        result = audit(deck(titled(1, "Wide", shape("", kind="table", sid="t", cols=9))))
        assert any("seven columns" in o.message for o in result.by_area(Area.LAYOUT))

    def test_normal_table_is_not_reported(self):
        result = audit(deck(titled(1, "Fine", shape("", kind="table", sid="t", cols=4))))
        assert not any("columns" in o.message for o in result.by_area(Area.LAYOUT))


class TestAccessibility:
    def test_image_only_slide_is_reported(self):
        picture = ShapeInfo(id="p", name="Pic", kind="picture",
                            x=0, y=0, cx=EMU_PER_INCH, cy=EMU_PER_INCH)
        result = audit(deck(SlideInfo(number=1, part_name="s1", shapes=[picture])))
        assert result.by_area(Area.ACCESSIBILITY)

    def test_slide_with_image_and_text_is_not_reported(self):
        picture = ShapeInfo(id="p", name="Pic", kind="picture",
                            x=0, y=0, cx=EMU_PER_INCH, cy=EMU_PER_INCH)
        result = audit(deck(titled(1, "Has words", picture)))
        assert not result.by_area(Area.ACCESSIBILITY)


class TestReporting:
    def test_report_includes_headline_metrics(self):
        text = audit(deck(titled(1, "One", shape("a b c"))), "demo.pptx").render()
        assert "demo.pptx" in text
        assert "words per slide" in text
        assert "quality gate" in text

    def test_clean_report_says_so(self):
        text = audit(deck(titled(1, "Revenue grew 38 percent"))).render()
        assert "No structural issues found" in text

    def test_where_summarises_long_slide_lists(self):
        slides = [SlideInfo(number=n, part_name=f"s{n}", shapes=[shape("body")])
                  for n in range(1, 12)]
        found = audit(deck(*slides)).by_area(Area.NARRATIVE)[0]
        assert "11 slides" in found.where


class TestRealDecks:
    def test_audits_a_real_deck_without_error(self):
        from pathlib import Path

        import pytest

        path = Path(r"C:/Users/mukun/Downloads/AgroLens - Project Phase-I final.pptx")
        if not path.is_file():
            pytest.skip("external corpus deck not present")
        result = audit(inspect(path), path.name)
        assert result.slide_count == 19
        assert result.observations, "a real student deck should have observations"
        assert all(o.suggestion for o in result.observations), "every finding needs an action"


def with_layout(number: int, title: str, layout: str, *extra: ShapeInfo) -> SlideInfo:
    slide = titled(number, title, *extra)
    slide.layout = layout
    return slide


class TestForeignSlides:
    """Slides that came from another deck.

    The pasted-together deck is the ordinary case. Deck-level checks say "five
    typefaces are in use" and stop, which tells a user their deck is
    inconsistent without telling them where to look.
    """

    def _consistency(self, result):
        return [o for o in result.observations if o.area is Area.CONSISTENCY]

    def test_a_minority_hardcoding_a_typeface_is_named(self):
        result = audit(deck(
            titled(1, "Revenue grew 38 percent", shape("Inherits the theme")),
            titled(2, "Margins improved", shape("Inherits the theme")),
            titled(3, "Churn fell to 1.9 percent", shape("Inherits the theme")),
            titled(4, "Pipeline is healthy", shape("Pasted in", font="Arial")),
        ))
        found = [o for o in self._consistency(result) if "hardcode" in o.message]
        assert found and found[0].slides == [4]

    def test_a_deck_that_hardcodes_throughout_is_not_flagged(self):
        """Consistent is consistent, even if it never uses the theme."""
        result = audit(deck(
            titled(1, "Revenue grew 38 percent", shape("A", font="Arial")),
            titled(2, "Margins improved", shape("B", font="Arial")),
            titled(3, "Churn fell to 1.9 percent", shape("C", font="Arial")),
        ))
        assert not [o for o in self._consistency(result) if "hardcode" in o.message]

    def test_a_theme_reference_is_not_a_hardcoded_font(self):
        """`+mn-lt` is the slide deferring to the template, not overriding it."""
        result = audit(deck(
            titled(1, "Revenue grew 38 percent", shape("A")),
            titled(2, "Margins improved", shape("B")),
            titled(3, "Churn fell to 1.9 percent", shape("C")),
            titled(4, "Pipeline is healthy", shape("D", font="+mn-lt")),
        ))
        assert not [o for o in self._consistency(result) if "hardcode" in o.message]

    def test_a_bare_majority_hardcoding_is_not_an_outlier(self):
        result = audit(deck(
            titled(1, "Revenue grew 38 percent", shape("A", font="Arial")),
            titled(2, "Margins improved", shape("B", font="Arial")),
            titled(3, "Churn fell to 1.9 percent", shape("C")),
        ))
        assert not [o for o in self._consistency(result) if "hardcode" in o.message]


class TestLayoutOutliers:
    def _layout_findings(self, result):
        return [o for o in result.observations
                if o.area is Area.CONSISTENCY and "layout" in o.message]

    def _deck_with_interior_outlier(self):
        """The outlier must be interior: slide 1 and the last are structural."""
        return deck(
            with_layout(1, "Revenue grew 38 percent", "Title and Content"),
            with_layout(2, "Margins improved", "Title and Content"),
            with_layout(3, "Churn fell to 1.9 percent", "Two Content"),
            with_layout(4, "Pipeline is healthy", "Title and Content"),
            with_layout(5, "Cash runway is 26 months", "Title and Content"),
        )

    def test_a_one_off_layout_is_named(self):
        found = self._layout_findings(audit(self._deck_with_interior_outlier()))
        assert found and found[0].slides == [3]

    def test_it_admits_the_finding_may_be_deliberate(self):
        """A one-off layout is often a divider. Say so rather than assert."""
        found = self._layout_findings(audit(self._deck_with_interior_outlier()))
        assert "deliberate divider" in found[0].suggestion

    def test_a_title_slide_layout_is_not_an_outlier(self):
        """It is unique by convention, and flagging it was a real false positive."""
        result = audit(deck(
            with_layout(1, "Revenue grew 38 percent", "Title Slide"),
            with_layout(2, "Margins improved", "Title and Content"),
            with_layout(3, "Churn fell to 1.9 percent", "Title and Content"),
            with_layout(4, "Pipeline is healthy", "Title and Content"),
        ))
        assert not self._layout_findings(result)

    def test_a_closing_slide_layout_is_not_an_outlier(self):
        result = audit(deck(
            with_layout(1, "Revenue grew 38 percent", "Title and Content"),
            with_layout(2, "Margins improved", "Title and Content"),
            with_layout(3, "Churn fell to 1.9 percent", "Title and Content"),
            with_layout(4, "Questions", "Closing"),
        ))
        assert not self._layout_findings(result)

    def test_a_deck_using_one_layout_throughout_is_not_flagged(self):
        result = audit(deck(
            with_layout(1, "Revenue grew 38 percent", "Title and Content"),
            with_layout(2, "Margins improved", "Title and Content"),
            with_layout(3, "Churn fell to 1.9 percent", "Title and Content"),
            with_layout(4, "Pipeline is healthy", "Title and Content"),
        ))
        assert not self._layout_findings(result)

    def test_a_deck_where_every_layout_is_unique_is_not_flagged(self):
        """Many layouts is a style, not a defect."""
        result = audit(deck(
            with_layout(1, "Revenue grew 38 percent", "A"),
            with_layout(2, "Margins improved", "B"),
            with_layout(3, "Churn fell to 1.9 percent", "C"),
            with_layout(4, "Pipeline is healthy", "D"),
        ))
        assert not self._layout_findings(result)

    def test_slides_without_a_layout_are_ignored(self):
        result = audit(deck(
            titled(1, "Revenue grew 38 percent"),
            titled(2, "Margins improved"),
            titled(3, "Churn fell to 1.9 percent"),
        ))
        assert not self._layout_findings(result)


class TestTypefaceSpellings:
    """The same font, typed two ways — found on a real deck.

    177 runs in "Century Gothic" and 89 in "Century gothic". Identical to a
    reader, two different strings to the file, and two separate problems to
    anything counting deviations by typeface.
    """

    def _findings(self, result):
        return [o for o in result.observations if "spelled" in o.message]

    def test_a_case_difference_is_caught(self):
        result = audit(deck(
            titled(1, "Revenue grew 38 percent", shape("A", font="Century Gothic")),
            titled(2, "Margins improved", shape("B", font="Century gothic")),
            titled(3, "Churn fell to 1.9 percent", shape("C", font="Century Gothic")),
        ))
        found = self._findings(result)
        assert found
        assert "Century Gothic" in found[0].message
        assert "Century gothic" in found[0].message

    def test_a_whitespace_difference_is_caught(self):
        result = audit(deck(
            titled(1, "Revenue grew 38 percent", shape("A", font="Arial")),
            titled(2, "Margins improved", shape("B", font="Arial ")),
            titled(3, "Churn fell to 1.9 percent", shape("C", font="Arial")),
        ))
        assert self._findings(result)

    def test_consistent_spelling_is_not_flagged(self):
        result = audit(deck(
            titled(1, "Revenue grew 38 percent", shape("A", font="Arial")),
            titled(2, "Margins improved", shape("B", font="Arial")),
            titled(3, "Churn fell to 1.9 percent", shape("C", font="Arial")),
        ))
        assert not self._findings(result)

    def test_genuinely_different_fonts_are_not_flagged(self):
        """Arial and Georgia are two fonts, not two spellings of one."""
        result = audit(deck(
            titled(1, "Revenue grew 38 percent", shape("A", font="Arial")),
            titled(2, "Margins improved", shape("B", font="Georgia")),
            titled(3, "Churn fell to 1.9 percent", shape("C", font="Arial")),
        ))
        assert not self._findings(result)

    def test_theme_references_are_ignored(self):
        result = audit(deck(
            titled(1, "Revenue grew 38 percent", shape("A", font="+mn-lt")),
            titled(2, "Margins improved", shape("B", font="+MN-LT")),
            titled(3, "Churn fell to 1.9 percent", shape("C", font="+mn-lt")),
        ))
        assert not self._findings(result)
