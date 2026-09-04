"""Command line surface.

Exit codes are part of the contract — this is meant to be usable in a script,
so "the deck failed verification" must be distinguishable from "the command was
wrong" without parsing prose.
"""

from __future__ import annotations

import pytest

from slide_wright.cli import EXIT_ERROR, EXIT_FINDINGS, EXIT_OK, main
from slide_wright.inspect import inspect


class TestInspect:
    def test_lists_slides_and_constructs(self, adversarial_deck, capsys):
        assert main(["inspect", str(adversarial_deck)]) == EXIT_OK
        out = capsys.readouterr().out
        assert "6 slides" in out
        assert "Trading comparables" in out
        assert "table:1" in out and "chart:1" in out

    def test_verbose_lists_shape_geometry(self, adversarial_deck, capsys):
        main(["inspect", str(adversarial_deck), "--verbose"])
        assert "id=" in capsys.readouterr().out


class TestAudit:
    def test_clean_deck_exits_zero(self, adversarial_deck, capsys):
        assert main(["audit", str(adversarial_deck)]) == EXIT_OK
        out = capsys.readouterr().out
        assert "DECK AUDIT" in out
        assert "No structural issues found" in out

    def test_gate_only_runs_just_the_delivery_gate(self, adversarial_deck, capsys):
        assert main(["audit", str(adversarial_deck), "--gate-only"]) == EXIT_OK
        out = capsys.readouterr().out
        assert "QUALITY GATE" in out
        assert "DECK AUDIT" not in out

    def test_structural_findings_exit_nonzero(self, tmp_path, capsys):
        """A deck with untitled slides is reportable even if the gate passes."""
        from pptx import Presentation
        from pptx.util import Inches

        prs = Presentation()
        for _ in range(3):
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
            box.text_frame.text = "Body text with no title above it"
        deck = tmp_path / "untitled.pptx"
        prs.save(str(deck))

        assert main(["audit", str(deck)]) == EXIT_FINDINGS
        assert "no title" in capsys.readouterr().out

    def test_findings_exit_nonzero(self, tmp_path, capsys):
        """A deck with content off the canvas must be reportable in a script."""
        from pptx import Presentation
        from pptx.util import Inches

        prs = Presentation()
        prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        box = slide.shapes.add_textbox(Inches(10), Inches(1), Inches(6), Inches(1))
        box.text_frame.text = "This box runs off the right-hand edge of the slide"
        broken = tmp_path / "broken.pptx"
        prs.save(str(broken))

        assert main(["audit", str(broken)]) == EXIT_FINDINGS
        assert "past the right edge" in capsys.readouterr().out


class TestProfile:
    def test_reports_difficulty(self, adversarial_deck, minimal_deck, capsys):
        assert main(["profile", str(adversarial_deck), str(minimal_deck)]) == EXIT_OK
        out = capsys.readouterr().out
        assert "synthetic-adversarial" in out and "synthetic-minimal" in out


class TestEdit:
    def test_applies_and_writes_output(self, adversarial_deck, tmp_path, capsys):
        table = next(s for s in inspect(adversarial_deck).all_shapes() if s.kind == "table")
        out = tmp_path / "out.pptx"
        code = main([
            "edit", str(adversarial_deck),
            "--set", f"3:{table.id}/r1/c1:9.4x=11.8x",
            "-o", str(out), "--workspace", str(tmp_path / "ws"),
        ])
        assert code == EXIT_OK
        assert "VERIFIED" in capsys.readouterr().out
        assert out.is_file()
        assert "11.8x" in inspect(out).slide(3).text

    def test_dry_run_changes_nothing(self, adversarial_deck, tmp_path, capsys):
        table = next(s for s in inspect(adversarial_deck).all_shapes() if s.kind == "table")
        before = adversarial_deck.read_bytes()
        code = main([
            "edit", str(adversarial_deck),
            "--set", f"3:{table.id}/r1/c1:9.4x=11.8x",
            "--dry-run", "--workspace", str(tmp_path / "ws"),
        ])
        assert code == EXIT_OK
        assert "dry run" in capsys.readouterr().out
        assert adversarial_deck.read_bytes() == before

    def test_numbers_lock_blocks_a_figure_edit(self, adversarial_deck, tmp_path, capsys):
        table = next(s for s in inspect(adversarial_deck).all_shapes() if s.kind == "table")
        code = main([
            "edit", str(adversarial_deck),
            "--set", f"3:{table.id}/r1/c1:9.4x=11.8x",
            "--lock", "numbers", "--workspace", str(tmp_path / "ws"),
        ])
        assert code == EXIT_FINDINGS
        assert "blocked by numbers lock" in capsys.readouterr().out

    def test_slide_lock_blocks_that_slide(self, adversarial_deck, tmp_path, capsys):
        table = next(s for s in inspect(adversarial_deck).all_shapes() if s.kind == "table")
        code = main([
            "edit", str(adversarial_deck),
            "--set", f"3:{table.id}/r1/c1:9.4x=11.8x",
            "--lock", "slide:3", "--workspace", str(tmp_path / "ws"),
        ])
        assert code == EXIT_FINDINGS

    def test_malformed_set_is_a_usage_error(self, adversarial_deck, tmp_path, capsys):
        code = main([
            "edit", str(adversarial_deck), "--set", "nonsense",
            "--workspace", str(tmp_path / "ws"),
        ])
        assert code == EXIT_ERROR
        assert "slide:target:before=after" in capsys.readouterr().err

    def test_no_changes_is_a_usage_error(self, adversarial_deck, tmp_path, capsys):
        code = main(["edit", str(adversarial_deck), "--workspace", str(tmp_path / "ws")])
        assert code == EXIT_ERROR
        assert "no changes" in capsys.readouterr().err


class TestVerify:
    def test_identical_files_verify(self, adversarial_deck, tmp_path, capsys):
        import shutil

        copy = tmp_path / "copy.pptx"
        shutil.copy(adversarial_deck, copy)
        assert main(["verify", str(adversarial_deck), str(copy)]) == EXIT_OK
        assert "100.00%" in capsys.readouterr().out


class TestRefusals:
    def test_hostile_file_is_refused_with_an_error_code(self, tmp_path, capsys):
        bad = tmp_path / "bad.pptx"
        bad.write_text("not a zip")
        assert main(["inspect", str(bad)]) == EXIT_ERROR
        assert "refused" in capsys.readouterr().err

    def test_missing_file_is_refused(self, tmp_path, capsys):
        assert main(["inspect", str(tmp_path / "nope.pptx")]) == EXIT_ERROR
