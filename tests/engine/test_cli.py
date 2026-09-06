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

    def test_verify_writes_nothing_beside_the_deck(self, adversarial_deck, tmp_path):
        """A read-only command must leave no trace next to a confidential file.

        `verify` used to open a session, which materialised a workspace —
        and therefore a copy of the user's deck — in the deck's own folder.
        Decks are confidential by default, so verifying one must not quietly
        duplicate it somewhere the user did not ask for.
        """
        import shutil

        folder = tmp_path / "client-materials"
        folder.mkdir()
        source = folder / "deck.pptx"
        shutil.copy(adversarial_deck, source)
        output = folder / "deck-edited.pptx"
        shutil.copy(adversarial_deck, output)

        before = sorted(p.name for p in folder.iterdir())
        assert main(["verify", str(source), str(output)]) == EXIT_OK
        assert sorted(p.name for p in folder.iterdir()) == before


class TestHistoryAndRevert:
    """The half of the loop the CLI did not expose.

    propose -> review -> apply -> verify -> revert is the product's promise.
    Until these commands existed a user could edit and verify from the command
    line but had no way to see what versions existed or to go back to one.
    """

    def _edit(self, deck, ws, before, after, message):
        table = next(s for s in inspect(deck).all_shapes() if s.kind == "table")
        return main(["edit", str(deck), "--workspace", str(ws),
                     "--set", f"3:{table.id}/r1/c1:{before}={after}",
                     "-m", message])

    def test_history_lists_every_version(self, adversarial_deck, tmp_path, capsys):
        ws = tmp_path / "ws"
        assert self._edit(adversarial_deck, ws, "9.4x", "11.8x", "Q3") == EXIT_OK
        assert self._edit(adversarial_deck, ws, "11.8x", "12.5x", "Q4") == EXIT_OK
        capsys.readouterr()

        assert main(["history", str(adversarial_deck), "--workspace", str(ws)]) == EXIT_OK
        out = capsys.readouterr().out
        assert "v000" in out and "v001" in out and "v002" in out
        assert "Q3" in out and "Q4" in out

    def test_revert_makes_an_earlier_version_current(
        self, adversarial_deck, tmp_path, capsys
    ):
        ws = tmp_path / "ws"
        self._edit(adversarial_deck, ws, "9.4x", "11.8x", "Q3")
        self._edit(adversarial_deck, ws, "11.8x", "12.5x", "Q4")
        capsys.readouterr()

        out_file = tmp_path / "back.pptx"
        code = main(["revert", str(adversarial_deck), "--workspace", str(ws),
                     "--to", "1", "-o", str(out_file)])
        assert code == EXIT_OK
        assert "v001" in capsys.readouterr().out

        table = next(s for s in inspect(out_file).all_shapes() if s.kind == "table")
        values = [r.text for r in table.runs]
        assert "11.8x" in values, "reverted to the wrong version"
        assert "12.5x" not in values, "the reverted deck still carries the later edit"

    def test_revert_defaults_to_the_original(self, adversarial_deck, tmp_path, capsys):
        ws = tmp_path / "ws"
        self._edit(adversarial_deck, ws, "9.4x", "11.8x", "Q3")
        capsys.readouterr()

        out_file = tmp_path / "orig.pptx"
        assert main(["revert", str(adversarial_deck), "--workspace", str(ws),
                     "-o", str(out_file)]) == EXIT_OK
        assert out_file.read_bytes() == adversarial_deck.read_bytes()

    def test_reverting_to_a_version_that_does_not_exist_is_refused(
        self, adversarial_deck, tmp_path, capsys
    ):
        ws = tmp_path / "ws"
        self._edit(adversarial_deck, ws, "9.4x", "11.8x", "Q3")
        capsys.readouterr()
        assert main(["revert", str(adversarial_deck), "--workspace", str(ws),
                     "--to", "9"]) == EXIT_ERROR
        assert "no version 9" in capsys.readouterr().err


class TestRefusals:
    def test_hostile_file_is_refused_with_an_error_code(self, tmp_path, capsys):
        bad = tmp_path / "bad.pptx"
        bad.write_text("not a zip")
        assert main(["inspect", str(bad)]) == EXIT_ERROR
        assert "refused" in capsys.readouterr().err

    def test_missing_file_is_refused(self, tmp_path, capsys):
        assert main(["inspect", str(tmp_path / "nope.pptx")]) == EXIT_ERROR


class TestRefresh:
    def test_updates_figures_from_a_csv(self, adversarial_deck, tmp_path, capsys):
        source = tmp_path / "comps.csv"
        source.write_text(
            "Company,EV/EBITDA,Margin,Growth\n"
            "Alpha Corp,11.8x,22.1%,18%\n"
            "Beta Industries,11.2x,19.8%,12%\n"
            "Gamma Holdings,8.7x,24.5%,21%\n",
            encoding="utf-8",
        )
        out = tmp_path / "refreshed.pptx"
        code = main([
            "refresh", str(adversarial_deck), "--source", str(source),
            "-o", str(out), "--workspace", str(tmp_path / "ws"),
        ])
        captured = capsys.readouterr().out
        assert code == EXIT_OK
        assert "comps.csv!B2" in captured, "every update must cite its source cell"
        assert "VERIFIED" in captured
        assert "11.8x" in inspect(out).slide(3).text

    def test_dry_run_shows_the_plan_only(self, adversarial_deck, tmp_path, capsys):
        source = tmp_path / "comps.csv"
        source.write_text("Company,EV/EBITDA\nAlpha Corp,11.8x\nBeta Industries,11.2x\n",
                          encoding="utf-8")
        before = adversarial_deck.read_bytes()
        code = main([
            "refresh", str(adversarial_deck), "--source", str(source),
            "--dry-run", "--workspace", str(tmp_path / "ws"),
        ])
        assert code == EXIT_OK
        assert "dry run" in capsys.readouterr().out
        assert adversarial_deck.read_bytes() == before

    def test_numbers_lock_blocks_a_refresh(self, adversarial_deck, tmp_path, capsys):
        source = tmp_path / "comps.csv"
        source.write_text("Company,EV/EBITDA\nAlpha Corp,11.8x\nBeta Industries,11.2x\n",
                          encoding="utf-8")
        code = main([
            "refresh", str(adversarial_deck), "--source", str(source),
            "--lock", "numbers", "--workspace", str(tmp_path / "ws"),
        ])
        assert code == EXIT_FINDINGS

    def test_unreadable_source_is_an_error(self, adversarial_deck, tmp_path, capsys):
        code = main([
            "refresh", str(adversarial_deck), "--source", str(tmp_path / "nope.csv"),
            "--workspace", str(tmp_path / "ws"),
        ])
        assert code == EXIT_ERROR
        assert "error" in capsys.readouterr().err


class TestBrand:
    def test_shows_the_profile_when_no_deck_is_given(self, adversarial_deck, capsys):
        assert main(["brand", str(adversarial_deck)]) == EXIT_OK
        assert "BRAND PROFILE" in capsys.readouterr().out

    def test_a_deck_conforms_to_its_own_template(self, adversarial_deck, capsys):
        assert main(["brand", str(adversarial_deck), str(adversarial_deck)]) == EXIT_OK
        assert "CONFORMS" in capsys.readouterr().out

    def test_off_template_deck_exits_nonzero(self, adversarial_deck, minimal_deck, tmp_path, capsys):
        from pptx import Presentation
        from pptx.util import Inches, Pt

        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
        run = box.text_frame.paragraphs[0].add_run()
        run.text = "Off-brand text"
        run.font.name = "Comic Sans MS"
        run.font.size = Pt(18)
        off = tmp_path / "off.pptx"
        prs.save(str(off))

        assert main(["brand", str(adversarial_deck), str(off)]) == EXIT_FINDINGS
        assert "Comic Sans MS" in capsys.readouterr().out
