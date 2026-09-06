"""Slide-Wright command line.

The Phase-0 product surface: a folder, a command line, and a change report.
Deliberately not a web application — there is no evidence yet that anyone wants
one, and the loop is what needs proving.

    slide-wright inspect  deck.pptx
    slide-wright audit    deck.pptx
    slide-wright verify   original.pptx edited.pptx
    slide-wright edit     deck.pptx --set 3:5/r1/c1:9.4x=11.8x -o out.pptx
    slide-wright profile  deck.pptx [deck.pptx ...]
    slide-wright refresh  deck.pptx --source comps.csv -o out.pptx
    slide-wright brand    house.potx deck.pptx
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from slide_wright import __version__
from slide_wright.apply import ApplyError
from slide_wright.audit import audit as audit_deck
from slide_wright.changeset import Change, Op
from slide_wright.corpus.profile import format_table, profile_many
from slide_wright.gate import check
from slide_wright.inspect import EMU_PER_INCH, inspect
from slide_wright.package import UnsafePackageError
from slide_wright.session import Session, SessionError

EXIT_OK, EXIT_FINDINGS, EXIT_ERROR = 0, 1, 2


def cmd_inspect(args) -> int:
    deck = inspect(args.deck)
    print(f"{Path(args.deck).name}")
    print(
        f"  {deck.slide_count} slides · "
        f"{deck.slide_width / EMU_PER_INCH:.2f} x {deck.slide_height / EMU_PER_INCH:.2f} in · "
        f"fonts {deck.theme_fonts.get('major', '?')}/{deck.theme_fonts.get('minor', '?')}"
    )
    print()
    for slide in deck.slides:
        kinds: dict[str, int] = {}
        for shape in slide.shapes:
            kinds[shape.kind] = kinds.get(shape.kind, 0) + 1
        census = " ".join(f"{k}:{v}" for k, v in sorted(kinds.items())) or "empty"
        title = slide.title or "(untitled)"
        print(f"  {slide.number:>3}  {title[:48]:<48} {slide.word_count:>4}w  {census}")
        if args.verbose:
            for shape in slide.shapes:
                pos = ""
                if shape.x is not None:
                    pos = (
                        f" @{shape.x / EMU_PER_INCH:.2f},{shape.y / EMU_PER_INCH:.2f}"
                        f" {shape.cx / EMU_PER_INCH:.2f}x{shape.cy / EMU_PER_INCH:.2f}in"
                    )
                text = f' "{shape.text[:40]}"' if shape.has_text else ""
                print(f"         id={shape.id:<4} {shape.kind:<12}{pos}{text}")
    return EXIT_OK


def cmd_audit(args) -> int:
    deck = inspect(args.deck)
    if args.gate_only:
        result = check(deck)
        print(result.render())
        return EXIT_OK if result.passed else EXIT_FINDINGS

    report = audit_deck(deck, Path(args.deck).name)
    print(report.render())
    if report.gate and report.gate.findings:
        print()
        print(report.gate.render())
    clean = not report.observations and (report.gate is None or report.gate.passed)
    return EXIT_OK if clean else EXIT_FINDINGS


def cmd_refresh(args) -> int:
    from slide_wright.refresh import plan_refresh
    from slide_wright.sources import SourceError, SourceSet, load

    sources = SourceSet()
    for spec in args.source:
        try:
            for table in load(spec):
                sources.add(table)
        except SourceError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_ERROR

    session = Session.open(args.deck, workspace=args.workspace)
    plan = plan_refresh(session.deck(), sources)
    print(plan.render())
    print()

    if not plan.updates:
        print("nothing to update — every matched figure already agrees with the source")
        return EXIT_OK
    if args.dry_run:
        print("dry run — nothing was applied")
        return EXIT_OK

    changeset = session.propose("refresh figures from source")
    for lock in args.lock or []:
        scope, _, target = lock.partition(":")
        try:
            changeset.lock(scope, target)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_ERROR
    for change in plan.to_changeset(str(session.current.path)).changes:
        changeset.add(change)
    changeset.approve_all()

    if not changeset.approved:
        print("all updates were blocked by locks; nothing to apply", file=sys.stderr)
        return EXIT_FINDINGS

    try:
        report = session.apply()
    except (SessionError, ApplyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    print(report.render())
    if not report.deliverable:
        print("\nnot delivered — verification failed", file=sys.stderr)
        return EXIT_FINDINGS
    if args.output:
        print(f"\nwrote {session.export(args.output)}")
    return EXIT_OK


def cmd_brand(args) -> int:
    from slide_wright.brand import check_conformance, read_profile

    profile = read_profile(args.template)
    if args.deck is None:
        print(profile.render())
        return EXIT_OK
    report = check_conformance(inspect(args.deck), profile, Path(args.deck).name)
    print(report.render())
    print()
    print(f"  conformance {report.score:.1f}% of {report.checked_runs} text run(s)")
    return EXIT_OK if report.conforms else EXIT_FINDINGS


def cmd_verify(args) -> int:
    session = Session.open(args.source, workspace=Path(args.source).parent / ".slidewright-tmp")
    report = session.verify(args.source, args.output)
    print(report.render())
    return EXIT_OK if report.deliverable else EXIT_FINDINGS


def cmd_profile(args) -> int:
    print(format_table(profile_many(args.decks)))
    return EXIT_OK


def cmd_edit(args) -> int:
    session = Session.open(args.deck, workspace=args.workspace)
    changeset = session.propose(args.message or "")

    for lock in args.lock or []:
        scope, _, target = lock.partition(":")
        try:
            changeset.lock(scope, target)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_ERROR

    for i, spec in enumerate(args.set or [], start=1):
        try:
            change = _parse_set(spec, i)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_ERROR
        changeset.add(change)

    if args.instruct:
        from slide_wright.llm.client import default_provider
        from slide_wright.planner import plan

        provider = default_provider()
        result = plan(
            session.deck(), args.instruct,
            deck_path=str(session.current.path), provider=provider,
        )
        if result.is_stub:
            print(
                "note: no GEMINI_API_KEY set, so no model was consulted. "
                "Copy .env.example to .env and add a free-tier key, "
                "or use --set to make changes directly.",
                file=sys.stderr,
            )
        for raw, reason in result.dropped:
            print(f"note: dropped proposed change ({raw.get('op', '?')}): {reason}",
                  file=sys.stderr)
        for change in result.changeset.changes:
            changeset.add(change)

    if not changeset.changes:
        print("error: no changes (use --set, or --instruct with a key)", file=sys.stderr)
        return EXIT_ERROR

    print(changeset.render())
    print()

    if args.dry_run:
        print("dry run — nothing was applied")
        return EXIT_OK

    changeset.approve_all()
    if not changeset.approved:
        print("all changes were blocked by locks; nothing to apply", file=sys.stderr)
        return EXIT_FINDINGS

    try:
        report = session.apply()
    except (SessionError, ApplyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    print(report.render())
    if not report.deliverable:
        print("\nnot delivered — verification failed", file=sys.stderr)
        return EXIT_FINDINGS

    if args.output:
        print(f"\nwrote {session.export(args.output)}")
    return EXIT_OK


def _parse_set(spec: str, index: int) -> Change:
    """Parse `slide:target:before=after` into a change.

    Targets ending in /r{row}/c{col} address a table cell; anything else
    addresses a shape's text.
    """
    head, sep, after = spec.partition("=")
    if not sep:
        raise ValueError(f"--set needs 'slide:target:before=after', got {spec!r}")
    parts = head.split(":", 2)
    if len(parts) != 3:
        raise ValueError(f"--set needs 'slide:target:before=after', got {spec!r}")
    slide_s, target, before = parts
    if not slide_s.isdigit():
        raise ValueError(f"slide must be a number, got {slide_s!r}")
    op = Op.SET_TABLE_CELL if "/r" in target and "/c" in target else Op.SET_TEXT
    return Change(id=f"c{index}", op=op, slide=int(slide_s), target=target,
                  before=before, after=after)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="slide-wright",
        description="Change what you asked. Preserve everything else. Prove it.",
    )
    parser.add_argument("--version", action="version", version=f"slide-wright {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("inspect", help="structural summary of a deck")
    p.add_argument("deck")
    p.add_argument("-v", "--verbose", action="store_true", help="list every shape")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("audit", help="what is wrong with this deck")
    p.add_argument("deck")
    p.add_argument("--gate-only", action="store_true",
                   help="only the delivery gate, not the structural audit")
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("refresh", help="update figures from a spreadsheet, with citations")
    p.add_argument("deck")
    p.add_argument("--source", action="append", required=True, metavar="FILE",
                   help=".csv or .xlsx; repeatable")
    p.add_argument("--lock", action="append", metavar="SCOPE[:TARGET]")
    p.add_argument("-o", "--output", help="write the verified deck here")
    p.add_argument("--workspace", help="where versions are kept")
    p.add_argument("--dry-run", action="store_true", help="show the plan and stop")
    p.set_defaults(func=cmd_refresh)

    p = sub.add_parser("brand", help="check a deck against a template")
    p.add_argument("template", help=".potx or .pptx whose theme is the authority")
    p.add_argument("deck", nargs="?", help="deck to check; omit to just show the profile")
    p.set_defaults(func=cmd_brand)

    p = sub.add_parser("verify", help="compare an output against its source")
    p.add_argument("source")
    p.add_argument("output")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("profile", help="measure how adversarial decks are")
    p.add_argument("decks", nargs="+")
    p.set_defaults(func=cmd_profile)

    p = sub.add_parser("edit", help="apply changes and verify the result")
    p.add_argument("deck")
    p.add_argument("--set", action="append", metavar="SLIDE:TARGET:BEFORE=AFTER",
                   help="a change; repeatable")
    p.add_argument("--instruct", metavar="TEXT",
                   help="describe the change in plain language (needs a model key)")
    p.add_argument("--lock", action="append", metavar="SCOPE[:TARGET]",
                   help="protect content: numbers, wording, layout, tables, "
                        "charts, media, slide:4, shape:7")
    p.add_argument("-o", "--output", help="write the verified deck here")
    p.add_argument("-m", "--message", help="what this edit is for")
    p.add_argument("--workspace", help="where versions are kept")
    p.add_argument("--dry-run", action="store_true", help="show the change set and stop")
    p.set_defaults(func=cmd_edit)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except UnsafePackageError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except (SessionError, ApplyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
