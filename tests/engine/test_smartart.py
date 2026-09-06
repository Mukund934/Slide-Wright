"""SmartArt.

The construct most likely to be silently destroyed, and the one every earlier
fidelity result was blind to — the corpus contained none until 6 Sep 2026.

Tests marked `fixtures` need the third-party corpus:

    python scripts/fetch_fixtures.py

They skip cleanly without it, because a test that fails on a fresh clone
teaches people to ignore failures.
"""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

import pytest

from slide_wright.apply import ApplyError, apply_changes
from slide_wright.changeset import Change, ChangeSet, Op
from slide_wright.fidelity import compare
from slide_wright.inspect import inspect
from slide_wright.package import Package
from slide_wright.smartart import (
    SmartArtUnsupported,
    assert_preserved,
    census,
    count_points,
    find_all,
    guard_edit,
    read_nodes,
)

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "third-party"


def fixture(name: str) -> Path:
    path = FIXTURES / name
    if not path.is_file():
        pytest.skip(f"{name} not present; run scripts/fetch_fixtures.py")
    return path


@pytest.fixture
def org_chart():
    return fixture("lo-smartart-org-chart.pptx")


@pytest.fixture
def picture_strip():
    """A diagram plus other editable text — the shape the preserve case needs."""
    return fixture("lo-smartart-picture-strip.pptx")


@pytest.fixture
def chart_types():
    """No diagram at all. A control for claims about what SmartArt causes."""
    return fixture("pypptx-chart-types.pptx")


@pytest.fixture
def three_diagrams():
    return fixture("lo-smartart-three-diagrams.pptx")


class TestDetection:
    def test_finds_the_diagram(self, org_chart):
        arts = find_all(org_chart)
        assert len(arts) == 1
        assert arts[0].complete, "all four required parts must be present"

    def test_reports_all_five_parts(self, org_chart):
        art = find_all(org_chart)[0]
        assert len(art.parts) == 5  # data, layout, quickStyle, colors, drawing
        assert art.data_part.startswith("ppt/diagrams/data")
        assert art.has_drawing_cache

    def test_handles_several_diagrams_in_one_deck(self, three_diagrams):
        """Part-numbering and rel-id collisions are where naive readers break."""
        arts = find_all(three_diagrams)
        assert len(arts) == 3
        assert len({a.data_part for a in arts}) == 3, "each diagram needs its own data part"

    def test_a_deck_without_smartart_reports_none(self, adversarial_deck):
        assert find_all(adversarial_deck) == []
        assert census(adversarial_deck)["diagrams"] == 0


class TestReadingTheModel:
    def test_reads_authored_text_from_the_data_part(self, org_chart):
        """Not from the drawing cache — that is a screenshot of the truth."""
        texts = find_all(org_chart)[0].text
        assert "Employee" in texts
        assert any("Manager" in t for t in texts)

    def test_paragraphs_are_separated(self, org_chart):
        """A node of "Manager" / "Second para" must not read as one word."""
        joined = " ".join(find_all(org_chart)[0].text)
        assert "ManagerSecond" not in joined

    def test_structure_is_counted_even_with_no_text(self):
        """A diagram of empty boxes still has structure worth preserving."""
        path = fixture("poi-smartart.pptx")
        counts = census(path)
        assert counts["nodes"] == 0, "this fixture genuinely has no authored text"
        assert counts["points"] > 20, "but it has real structure"

    def test_point_count_exceeds_text_node_count(self, org_chart):
        counts = census(org_chart)
        assert counts["points"] > counts["nodes"]


class TestRefusal:
    """Editing a diagram is refused, precisely. An honest no beats a silent yes."""

    def test_guard_refuses_a_diagram_target(self, org_chart):
        art = find_all(org_chart)[0]
        with pytest.raises(SmartArtUnsupported, match="SmartArt"):
            guard_edit(org_chart, art.slide, art.shape_id)

    def test_refusal_explains_why_and_what_to_do(self, org_chart):
        art = find_all(org_chart)[0]
        with pytest.raises(SmartArtUnsupported) as caught:
            guard_edit(org_chart, art.slide, art.shape_id)
        message = str(caught.value)
        assert "drawing cache" in message
        assert "PowerPoint" in message

    def test_guard_ignores_other_shapes(self, picture_strip):
        deck = inspect(picture_strip)
        other = next(s for s in deck.all_shapes() if s.has_text)
        guard_edit(picture_strip, 1, other.id)  # must not raise

    def test_applier_refuses_a_diagram_edit(self, org_chart):
        art = find_all(org_chart)[0]
        cs = ChangeSet(deck=str(org_chart))
        cs.add(Change(id="c1", op=Op.SET_TEXT, slide=art.slide,
                      target=art.shape_id, before="Employee", after="Contractor"))
        cs.approve_all()
        with tempfile.TemporaryDirectory() as tmp:
            with pytest.raises(ApplyError, match="SmartArt"):
                apply_changes(org_chart, cs, Path(tmp) / "out.pptx")


class TestPreservation:
    """Edit something else; the diagram must come through untouched."""

    def test_editing_a_caption_leaves_the_diagram_intact(self, picture_strip):
        deck = inspect(picture_strip)
        target = next(s for s in deck.all_shapes() if s.has_text)
        cs = ChangeSet(deck=str(picture_strip))
        cs.add(Change(id="c1", op=Op.SET_TEXT, slide=1, target=target.id,
                      before=target.text, after="Slide-Wright marker"))
        cs.approve_all()

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.pptx"
            apply_changes(picture_strip, cs, out)

            before, after = census(picture_strip), census(out)
            assert after == before, "the diagram must be byte-for-byte unaffected"

            report = compare(picture_strip, out)
            assert len(report.changed) == 1
            assert report.fidelity_score > 95.0

    def test_diagram_media_relationships_survive(self, picture_strip):
        """This fixture's diagram parts reference images — the rel-rewrite trap."""
        deck = inspect(picture_strip)
        target = next(s for s in deck.all_shapes() if s.has_text)
        cs = ChangeSet(deck=str(picture_strip))
        cs.add(Change(id="c1", op=Op.SET_TEXT, slide=1, target=target.id,
                      before=target.text, after="marker"))
        cs.approve_all()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.pptx"
            apply_changes(picture_strip, cs, out)
            rep = compare(picture_strip, out)
            assert rep.output_census.media_parts == rep.source_census.media_parts
            assert not rep.native_losses


class TestAssertPreserved:
    def test_identical_packages_pass(self, org_chart):
        assert_preserved(org_chart, org_chart)  # must not raise

    def test_a_stripped_diagram_is_caught(self, org_chart, tmp_path):
        gutted = tmp_path / "gutted.pptx"
        with zipfile.ZipFile(org_chart) as zin, zipfile.ZipFile(gutted, "w") as zout:
            for info in zin.infolist():
                if info.filename.startswith("ppt/diagrams/"):
                    continue
                zout.writestr(info.filename, zin.read(info.filename))
        with pytest.raises(SmartArtUnsupported, match="damaged"):
            assert_preserved(org_chart, gutted)

    def test_losing_only_structure_is_caught(self, org_chart, tmp_path):
        """Text could survive while every box vanishes. Counted separately."""
        from lxml import etree

        from slide_wright.smartart import DGM_NS

        hollow = tmp_path / "hollow.pptx"
        art = find_all(org_chart)[0]
        with zipfile.ZipFile(org_chart) as zin, zipfile.ZipFile(hollow, "w") as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename == art.data_part:
                    # Remove real points, keeping the XML well-formed — the
                    # realistic failure is a valid file with content missing.
                    root = etree.fromstring(data)
                    for pt in list(root.iter(f"{{{DGM_NS}}}pt"))[:10]:
                        pt.getparent().remove(pt)
                    data = etree.tostring(root, xml_declaration=True, encoding="UTF-8")
                zout.writestr(info.filename, data)
        with pytest.raises(SmartArtUnsupported):
            assert_preserved(org_chart, hollow)

    def test_a_corrupted_diagram_part_is_damage_not_a_crash(self, org_chart, tmp_path):
        """Malformed XML must be reported as damage, never raised as a parser error."""
        broken = tmp_path / "broken.pptx"
        art = find_all(org_chart)[0]
        with zipfile.ZipFile(org_chart) as zin, zipfile.ZipFile(broken, "w") as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename == art.data_part:
                    data = b"<dgm:dataModel><unclosed>"
                zout.writestr(info.filename, data)
        assert census(broken)["malformed"] == 1
        with pytest.raises(SmartArtUnsupported):
            assert_preserved(org_chart, broken)


class TestRoundTripEngineLimitation:
    """A measured limitation — recorded rather than papered over.

    The heavy round-trip engine refuses every deck containing SmartArt at the
    authoring projection. Removing only the diagram from a fixture turns that
    refusal into an acceptance, so a diagram is *sufficient* to trigger it.

    It is not *necessary*. Diagram-free decks are refused with a byte-identical
    message, so the constraint belongs to the engine's font-family projection,
    not to SmartArt. Both facts are pinned below: calling this a SmartArt
    restriction would misdirect anyone debugging a refused chart-only deck.

    The in-place applier handles all of these decks, which is why it is the
    primary path. See ADR-0007.
    """

    def test_in_place_path_handles_smartart_decks(self, picture_strip):
        deck = inspect(picture_strip)
        target = next(s for s in deck.all_shapes() if s.has_text)
        cs = ChangeSet(deck=str(picture_strip))
        cs.add(Change(id="c1", op=Op.SET_TEXT, slide=1, target=target.id,
                      before=target.text, after="marker"))
        cs.approve_all()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.pptx"
            result = apply_changes(picture_strip, cs, out)
            assert result.ok
            assert census(out)["diagrams"] == census(picture_strip)["diagrams"]

    @pytest.mark.engine
    def test_round_trip_engine_refuses_smartart(self, org_chart):
        from slide_wright.engines.pptmaster import EngineError, PptMasterEngine

        try:
            engine = PptMasterEngine()
        except EngineError:
            pytest.skip("round-trip engine not vendored on this machine")
        with tempfile.TemporaryDirectory() as tmp:
            result = engine.ingest(org_chart, Path(tmp) / "ws")
            assert not result.ok, (
                "engine unexpectedly accepted a SmartArt deck — if this now "
                "passes, the limitation is fixed and the docs need updating"
            )

    @pytest.mark.engine
    def test_round_trip_engine_also_refuses_a_deck_with_no_diagram(self, chart_types):
        """The refusal is not SmartArt-specific, and the record must not say so."""
        from slide_wright.engines.pptmaster import EngineError, PptMasterEngine

        assert not find_all(Package.open(chart_types)), "control deck gained a diagram"
        try:
            engine = PptMasterEngine()
        except EngineError:
            pytest.skip("round-trip engine not vendored on this machine")
        with tempfile.TemporaryDirectory() as tmp:
            result = engine.ingest(chart_types, Path(tmp) / "ws")
            assert not result.ok, (
                "a diagram-free deck was accepted — if every diagram-free deck "
                "now passes, SmartArt really is the sole cause and ADR-0007's "
                "correction should be revisited"
            )
            assert "font-family" in (result.stderr or ""), (
                "refused for a different reason than the SmartArt decks; "
                "ADR-0007 claims the message is identical"
            )


class TestPackageLevel:
    def test_count_points_matches_the_data_part(self, org_chart):
        pkg = Package.open(org_chart)
        art = find_all(pkg)[0]
        assert count_points(pkg, art.data_part) == census(org_chart)["points"]

    def test_read_nodes_is_stable(self, org_chart):
        pkg = Package.open(org_chart)
        art = find_all(pkg)[0]
        first = [n.text for n in read_nodes(pkg, art.data_part)]
        second = [n.text for n in read_nodes(pkg, art.data_part)]
        assert first == second
