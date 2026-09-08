"""Brand conformance against a template.

The template is the authority. These tests care that deviations are reported
*against* it and never averaged with it — a tool that decides the deck's most
common font is "the brand" would ratify the drift it exists to catch.
"""

from __future__ import annotations

import zipfile

import pytest

from slide_wright.brand import (
    BrandProfile,
    TemplateError,
    check_conformance,
    compare_profiles,
    dominant_fonts,
    is_theme_reference,
    plan_conformance,
    read_profile,
)
from slide_wright.changeset import Op, Origin
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


class TestThemeReferencesAreNotDrift:
    """`typeface="+mn-lt"` is a reference to the theme font, not a font.

    A run carrying one already follows the template in the only way that
    survives a template change. Reporting it as drift is a false positive, and
    "correcting" it to a literal font name would detach the run from the theme —
    damaging a deck that was already right, automatically, while reporting
    success.
    """

    def test_theme_references_are_recognised(self):
        for ref in ("+mn-lt", "+mj-lt", "+mn-ea", "+mj-cs"):
            assert is_theme_reference(ref), ref

    def test_real_typefaces_are_not(self):
        for font in ("Arial", "Calibri", "Georgia", "", None):
            assert not is_theme_reference(font)

    def test_a_theme_reference_is_not_reported_as_drift(self):
        profile = BrandProfile(source="t.potx", major_font="Calibri",
                               minor_font="Calibri")
        deck = _deck_with_fonts(["+mn-lt", "+mj-lt"])
        assert check_conformance(deck, profile).conforms

    def test_a_theme_reference_is_never_corrected(self):
        profile = BrandProfile(source="t.potx", major_font="Calibri",
                               minor_font="Calibri")
        deck = _deck_with_fonts(["+mn-lt", "Arial"])
        plan = plan_conformance(deck, profile)
        assert [c.before for c in plan.changes] == ["Arial"]


class TestConformancePlan:
    PROFILE = BrandProfile(source="t.potx", major_font="Georgia",
                           minor_font="Georgia")

    def test_an_off_template_font_is_planned_for_correction(self):
        plan = plan_conformance(_deck_with_fonts(["Arial"]), self.PROFILE)
        assert len(plan.changes) == 1
        change = plan.changes[0]
        assert change.op is Op.SET_FONT
        assert change.before == "Arial"
        assert change.origin is Origin.RULE, "a deterministic rule, not a model"

    def test_corrections_target_the_theme_reference_not_a_font_name(self):
        """Writing "Georgia" would look conformant and stay just as detached.

        Change the template later and every run corrected to a literal name is
        missed all over again. A reference is what a slide authored in this
        template carries, so it is what a corrected slide should carry.
        """
        plan = plan_conformance(_deck_with_fonts(["Arial"]), self.PROFILE)
        assert plan.changes[0].after == "+mn-lt"

    def test_a_title_defers_to_the_major_font(self):
        deck = _deck_with_fonts(["Arial"], placeholder="title")
        plan = plan_conformance(deck, self.PROFILE)
        assert plan.changes[0].after == "+mj-lt", "headings follow the major font"

    def test_a_run_hardcoding_the_templates_own_font_is_corrected(self):
        """Indistinguishable to look at, and still detached from the template."""
        plan = plan_conformance(_deck_with_fonts(["Georgia"]), self.PROFILE)
        assert len(plan.changes) == 1
        assert plan.changes[0].before == "Georgia"
        assert plan.changes[0].after == "+mn-lt"
        assert "hardcodes" in plan.changes[0].rationale

    def test_a_deck_already_using_references_plans_nothing(self):
        assert plan_conformance(_deck_with_fonts(["+mn-lt"]), self.PROFILE).empty

    def test_a_run_with_no_explicit_font_is_left_alone(self):
        """It already defers to the layout, which is what this restores."""
        assert plan_conformance(_deck_with_fonts([None]), self.PROFILE).empty

    def test_each_change_addresses_one_run(self):
        plan = plan_conformance(_deck_with_fonts(["Georgia", "Arial"]), self.PROFILE)
        assert [c.target for c in plan.changes] == ["7/run/0", "7/run/1"]

    def test_whitespace_runs_are_corrected_too(self):
        """Otherwise the fix cannot satisfy its own report."""
        deck = _deck_with_fonts(["Arial", "Arial"], texts=["Hello", "   "])
        assert len(plan_conformance(deck, self.PROFILE).changes) == 2

    def test_a_template_naming_no_font_corrects_nothing(self):
        plan = plan_conformance(_deck_with_fonts(["Arial"]),
                                BrandProfile(source="t.potx"))
        assert plan.empty and plan.skipped

    def test_the_plan_says_it_changes_no_content(self):
        rendered = plan_conformance(_deck_with_fonts(["Arial"]), self.PROFILE).render()
        assert "No word or number is changed" in rendered


def _deck_with_fonts(fonts: list, texts: list[str] | None = None,
                     placeholder: str | None = None) -> DeckInfo:
    """A one-slide deck whose single text box carries the given run fonts."""
    texts = texts or [f"Run {i}" for i in range(len(fonts))]
    shape = ShapeInfo(
        id="7", name="Body", kind="shape", placeholder_type=placeholder,
        runs=[TextRun(text=t, font=f) for t, f in zip(texts, fonts)],
    )
    return DeckInfo(slides=[SlideInfo(number=1, part_name="ppt/slides/slide1.xml",
                                      shapes=[shape])])


class TestATemplateThatCannotBeRead:
    """A damaged theme raised lxml's own `XMLSyntaxError`.

    The API wrapped it; the CLI did not, so it escaped `main()` and printed a
    traceback at whoever ran the command. A message that depends on which
    surface you came through is not a message, so the error is typed where it
    is raised and both surfaces get the same sentence.
    """

    def _rebuild(self, deck, out, replace=None, drop=()):
        with zipfile.ZipFile(deck) as zin, zipfile.ZipFile(out, "w") as zout:
            for info in zin.infolist():
                if any(info.filename.startswith(d) for d in drop):
                    continue
                data = zin.read(info.filename)
                if replace and info.filename.startswith(replace[0]):
                    data = replace[1]
                zout.writestr(info.filename, data)
        return out

    def test_a_theme_that_will_not_parse_is_refused_by_name(
        self, adversarial_deck, tmp_path
    ):
        broken = self._rebuild(adversarial_deck, tmp_path / "bad.pptx",
                               replace=("ppt/theme/theme", b"nope"))
        with pytest.raises(TemplateError, match="bad.pptx"):
            read_profile(broken)

    def test_the_refusal_names_the_part_and_the_reason(self, adversarial_deck, tmp_path):
        broken = self._rebuild(adversarial_deck, tmp_path / "bad.pptx",
                               replace=("ppt/theme/theme", b"nope"))
        with pytest.raises(TemplateError) as caught:
            read_profile(broken)
        assert "ppt/theme/theme" in str(caught.value)

    def test_it_is_a_ValueError_so_the_cli_already_handles_it(self):
        """Every CLI command taking a template turns a `ValueError` into a
        one-line `error:`. Inheriting is what makes this reach a person."""
        assert issubclass(TemplateError, ValueError)

    def test_no_theme_at_all_is_not_an_error(self, adversarial_deck, tmp_path):
        """A different situation, and only one of them is broken. An empty
        profile is honest, and `plan_conformance` says the template declares
        nothing to conform to rather than reporting zero corrections as if the
        deck already matched."""
        bare = self._rebuild(adversarial_deck, tmp_path / "bare.pptx",
                             drop=("ppt/theme/",))
        profile = read_profile(bare)
        assert profile.fonts == set()

    def test_and_the_plan_says_so_rather_than_reporting_nothing(
        self, adversarial_deck, tmp_path
    ):
        bare = self._rebuild(adversarial_deck, tmp_path / "bare.pptx",
                             drop=("ppt/theme/",))
        plan = plan_conformance(inspect(adversarial_deck), read_profile(bare))
        assert not plan.changes
        assert any("nothing" in s or "no fonts" in s for s in plan.skipped)

    def test_one_unreadable_layout_does_not_condemn_the_template(
        self, adversarial_deck, tmp_path
    ):
        """The fonts and palette come from the theme, which is what conformance
        uses. A layout that will not parse costs its name from a list."""
        odd = self._rebuild(adversarial_deck, tmp_path / "odd.pptx",
                            replace=("ppt/slideLayouts/slideLayout1.xml", b"nope"))
        profile = read_profile(odd)
        assert profile.minor_font
