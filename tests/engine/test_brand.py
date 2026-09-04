"""Brand conformance against a template.

The template is the authority. These tests care that deviations are reported
*against* it and never averaged with it — a tool that decides the deck's most
common font is "the brand" would ratify the drift it exists to catch.
"""

from __future__ import annotations

from slide_wright.brand import (
    BrandProfile,
    check_conformance,
    compare_profiles,
    dominant_fonts,
    read_profile,
)
from slide_wright.inspect import (
    EMU_PER_INCH,
    DeckInfo,
    ShapeInfo,
    SlideInfo,
    TextRun,
    inspect,
)

W, H = int(13.333 * EMU_PER_INCH), int(7.5 * EMU_PER_INCH)

TEMPLATE = BrandProfile(
    source="house.potx",
    major_font="Georgia",
    minor_font="Arial",
    theme_colors={"dk1": "111827", "lt1": "FFFFFF", "accent1": "0B5D3B"},
)


def run_shape(text="Body", *, font=None, colour=None, size=18.0, ph=None, sid="1"):
    return ShapeInfo(
        id=sid, name="S", kind="shape", placeholder_type=ph,
        x=0, y=0, cx=EMU_PER_INCH, cy=EMU_PER_INCH,
        runs=[TextRun(text=text, size_pt=size, font=font, color=colour)],
    )


def deck(*shapes: ShapeInfo, slides: int = 1) -> DeckInfo:
    return DeckInfo(
        slide_width=W, slide_height=H,
        slides=[SlideInfo(number=i + 1, part_name=f"s{i}", shapes=list(shapes))
                for i in range(slides)],
    )


class TestProfileExtraction:
    def test_reads_fonts_from_the_theme(self, adversarial_deck):
        profile = read_profile(adversarial_deck)
        assert profile.major_font
        assert profile.minor_font

    def test_reads_the_colour_scheme(self, adversarial_deck):
        profile = read_profile(adversarial_deck)
        assert profile.theme_colors
        assert all(len(v) == 6 for v in profile.theme_colors.values())

    def test_palette_is_uppercase_hex(self, adversarial_deck):
        assert all(c == c.upper() for c in read_profile(adversarial_deck).palette)

    def test_reads_layout_names(self, adversarial_deck):
        names = read_profile(adversarial_deck).layout_names
        assert names and "Title Slide" in names

    def test_render_is_readable(self, adversarial_deck):
        text = read_profile(adversarial_deck).render()
        assert "BRAND PROFILE" in text and "fonts" in text


class TestConformance:
    def test_a_conforming_deck_reports_no_deviations(self):
        report = check_conformance(
            deck(run_shape(font="Georgia", colour="111827")), TEMPLATE
        )
        assert report.conforms
        assert report.score == 100.0
        assert "CONFORMS" in report.render()

    def test_off_template_font_is_reported(self):
        report = check_conformance(deck(run_shape(font="Comic Sans MS")), TEMPLATE)
        fonts = [d for d in report.deviations if d.kind == "font"]
        assert fonts and "Comic Sans MS" in fonts[0].detail

    def test_font_deviation_suggests_the_template_fonts(self):
        report = check_conformance(deck(run_shape(font="Comic Sans MS")), TEMPLATE)
        assert "Arial" in report.deviations[0].suggestion
        assert "Georgia" in report.deviations[0].suggestion

    def test_off_palette_colour_is_reported(self):
        report = check_conformance(deck(run_shape(colour="FF00FF")), TEMPLATE)
        colours = [d for d in report.deviations if d.kind == "colour"]
        assert colours and "FF00FF" in colours[0].detail

    def test_palette_matching_ignores_case(self):
        report = check_conformance(deck(run_shape(colour="0b5d3b")), TEMPLATE)
        assert not [d for d in report.deviations if d.kind == "colour"]

    def test_deviations_list_every_affected_slide(self):
        report = check_conformance(
            deck(run_shape(font="Comic Sans MS"), slides=4), TEMPLATE
        )
        assert report.deviations[0].slides == [1, 2, 3, 4]

    def test_slides_are_not_double_counted(self):
        multi = deck(
            run_shape(font="Comic Sans MS", sid="1"),
            run_shape(font="Comic Sans MS", sid="2"),
        )
        assert check_conformance(multi, TEMPLATE).deviations[0].slides == [1]


class TestTemplateIsTheAuthority:
    def test_a_deck_that_uniformly_ignores_the_template_still_deviates(self):
        """The commonest font in the deck must never be mistaken for the brand."""
        wrong = deck(run_shape(font="Comic Sans MS"), slides=20)
        report = check_conformance(wrong, TEMPLATE)
        assert not report.conforms
        assert report.score < 100.0

    def test_dominant_fonts_describes_the_deck_not_the_brand(self):
        d = deck(run_shape(font="Comic Sans MS"), slides=5)
        assert dominant_fonts(d)[0][0] == "Comic Sans MS"
        # …and that is still a deviation.
        assert not check_conformance(d, TEMPLATE).conforms


class TestTitleSizes:
    def test_inconsistent_title_sizes_are_reported(self):
        slides = []
        for i, size in enumerate((32.0, 32.0, 24.0), start=1):
            title = run_shape("T", ph="title", size=size, font="Georgia", sid=str(i))
            slides.append(SlideInfo(number=i, part_name=f"s{i}", shapes=[title]))
        report = check_conformance(DeckInfo(slide_width=W, slide_height=H, slides=slides),
                                   TEMPLATE)
        sizes = [d for d in report.deviations if d.kind == "title-size"]
        assert sizes
        assert "32pt is the deck's norm" in sizes[0].detail
        assert sizes[0].slides == [3]

    def test_uniform_title_sizes_are_silent(self):
        slides = [
            SlideInfo(number=i, part_name=f"s{i}",
                      shapes=[run_shape("T", ph="title", size=32.0, font="Georgia", sid=str(i))])
            for i in range(1, 4)
        ]
        report = check_conformance(DeckInfo(slide_width=W, slide_height=H, slides=slides),
                                   TEMPLATE)
        assert not [d for d in report.deviations if d.kind == "title-size"]


class TestProfileComparison:
    def test_reports_font_changes(self):
        other = BrandProfile(major_font="Inter", minor_font="Arial")
        notes = compare_profiles(TEMPLATE, other)
        assert any("heading font" in n for n in notes)
        assert not any("body font" in n for n in notes)

    def test_reports_colour_changes(self):
        other = BrandProfile(
            major_font="Georgia", minor_font="Arial",
            theme_colors={"dk1": "111827", "lt1": "FFFFFF", "accent1": "AA0000"},
        )
        assert any("accent1" in n for n in compare_profiles(TEMPLATE, other))

    def test_identical_profiles_differ_in_nothing(self):
        assert compare_profiles(TEMPLATE, TEMPLATE) == []


class TestRealDeck:
    def test_finds_drift_in_a_real_deck(self, adversarial_deck):
        from pathlib import Path

        import pytest

        real = Path(r"C:/Users/mukun/Downloads/HACK4CROWN.pptx")
        if not real.is_file():
            pytest.skip("external corpus deck not present")
        report = check_conformance(
            inspect(real), read_profile(adversarial_deck), real.name
        )
        assert not report.conforms, "a real deck rarely matches a foreign template"
        assert 0 <= report.score <= 100
        assert all(d.suggestion for d in report.deviations)

    def test_a_deck_conforms_to_its_own_template(self, adversarial_deck):
        report = check_conformance(
            inspect(adversarial_deck), read_profile(adversarial_deck)
        )
        assert report.conforms, report.render()
