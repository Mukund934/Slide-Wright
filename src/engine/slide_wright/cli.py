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
    slide-wright propose  deck.pptx --instruct "..." -o changes.json
    slide-wright review   changes.json --approve c1 --reject c2
    slide-wright apply    deck.pptx changes.json -o out.pptx
    slide-wright tidy     deck.pptx -o out.pptx
    slide-wright align    deck.pptx --fix
    slide-wright diff     before.pptx after.pptx
    slide-wright history  deck.pptx
    slide-wright revert   deck.pptx --to 1
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from slide_wright import __version__
from slide_wright.apply import ApplyError
from slide_wright.audit import audit as audit_deck
from slide_wright.changeset import Change, ChangeSet, Op
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
    from slide_wright.brand import (
        TemplateError,
        check_conformance,
        plan_conformance,
        read_profile,
    )

    try:
        profile = read_profile(args.template)
    except (TemplateError, UnsafePackageError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if args.deck is None:
        print(profile.render())
        return EXIT_OK

    if args.fix:
        return _fix_conformance(args, profile)

    report = check_conformance(inspect(args.deck), profile, Path(args.deck).name)
    print(report.render())
    print()
    print(f"  conformance {report.score:.1f}% of {report.checked_runs} text run(s)")
    fixable = [d for d in report.deviations if d.kind == "font"]
    if fixable:
        print(f"  --fix can correct {len(fixable)} typeface deviation(s) "
              "without changing any content")
    other = [d for d in report.deviations if d.kind != "font"]
    if other:
        # Say what --fix will not touch, so nobody runs it twice expecting a
        # clean report. Colour and size corrections are judgement calls that
        # need a decision about which theme colour was intended.
        kinds = ", ".join(sorted({d.kind for d in other}))
        print(f"  {len(other)} deviation(s) --fix does not correct ({kinds})")
    return EXIT_OK if report.conforms else EXIT_FINDINGS


def _fix_conformance(args, profile) -> int:
    """Correct template drift, and prove no word or number moved.

    The promise here is narrower and stronger than "make it match the
    template": *change every typeface that does not conform, change nothing
    else at all*. The second half is checked rather than asserted -- a
    conformance pass that quietly reflowed a number would be far worse than one
    that did nothing.
    """
    from slide_wright.brand import plan_conformance
    from slide_wright.diff import diff as deck_diff

    session = Session.open(args.deck, workspace=args.workspace)
    plan = plan_conformance(session.deck(), profile, Path(args.deck).name)
    print(plan.render())
    print()

    if plan.empty:
        return EXIT_OK
    if args.dry_run:
        print("dry run — nothing was applied")
        return EXIT_OK

    changeset = session.propose(f"conform to {Path(args.template).name}")
    for lock in args.lock or []:
        scope, _, target = lock.partition(":")
        try:
            changeset.lock(scope, target)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_ERROR
    for change in plan.changes:
        changeset.add(change)
    changeset.approve_all()

    if not changeset.approved:
        print("every correction was blocked by a lock; nothing to apply", file=sys.stderr)
        return EXIT_FINDINGS

    before = session.current.path
    try:
        report = session.apply(f"conform to {Path(args.template).name}")
    except (SessionError, ApplyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    # The guarantee, enforced. A formatting pass that changed a word or a
    # number has done the one thing it promised not to do.
    content = deck_diff(before, session.current.path).content_deltas
    if content:
        print("REFUSED — a formatting pass changed content:", file=sys.stderr)
        for delta in content[:5]:
            print(f"    · slide {delta.slide} — {delta.description}", file=sys.stderr)
        return EXIT_ERROR

    print(report.render())
    print()
    print(f"  {len(changeset.applied)} run(s) corrected · "
          f"0 words or numbers changed, verified")
    if args.output:
        print(f"wrote {session.export(args.output)}")
    return EXIT_OK


def cmd_verify(args) -> int:
    """Compare two decks. Reads only — nothing is written anywhere.

    This deliberately does not open a Session. A session materialises a
    workspace next to the deck, which for `verify` meant dropping a copy of
    the user's file into a folder beside it. Decks are confidential by
    default; a read-only command must leave no trace on disk.
    """
    from slide_wright.fidelity import compare
    from slide_wright.report import build

    report = build(compare(args.source, args.output))
    print(report.render())
    return EXIT_OK if report.deliverable else EXIT_FINDINGS


def _build_changes(session, args, changeset) -> int | None:
    """Fill a change set from --set and --instruct. Returns an exit code on error."""
    for lock in args.lock or []:
        scope, _, target = lock.partition(":")
        try:
            changeset.lock(scope, target)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_ERROR

    for i, spec in enumerate(args.set or [], start=1):
        try:
            changeset.add(_parse_set(spec, i))
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_ERROR

    if getattr(args, "instruct", None):
        from slide_wright.llm.client import default_provider
        from slide_wright.planner import plan

        result = plan(
            session.deck(), args.instruct,
            deck_path=str(session.current.path), provider=default_provider(),
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
    return None


def cmd_propose(args) -> int:
    """Work out what to change and write it down. Nothing is applied.

    The change set is the reviewable artifact: a reviewer reads it, approves
    what they want, and only then is anything written to a deck.
    """
    session = Session.open(args.deck, workspace=args.workspace)
    changeset = session.propose(args.message or args.instruct or "")

    failed = _build_changes(session, args, changeset)
    if failed is not None:
        return failed
    if not changeset.changes:
        print("error: no changes (use --set, or --instruct with a key)", file=sys.stderr)
        return EXIT_ERROR

    print(changeset.render())
    path = changeset.save(args.output or session.workspace / "changes.json")
    print()
    print(f"wrote {path}")
    print(f"review it, then: slide-wright apply {args.deck} {path} -o out.pptx")
    return EXIT_OK


def cmd_review(args) -> int:
    """Approve or reject individual changes in a saved change set."""
    path = Path(args.changes)
    try:
        changeset = ChangeSet.from_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"error: cannot read change set: {exc}", file=sys.stderr)
        return EXIT_ERROR

    known = {c.id for c in changeset.changes}
    asked = set(args.approve or []) | set(args.reject or [])
    unknown = sorted(asked - known)
    if unknown:
        # Silently ignoring an id means a reviewer believes they rejected
        # something they did not.
        print(f"error: no such change(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"       this change set has: {', '.join(sorted(known))}", file=sys.stderr)
        return EXIT_ERROR

    if args.approve:
        changeset.approve(*args.approve)
    if args.reject:
        for cid in args.reject:
            changeset.reject(cid)
    if args.approve_all:
        changeset.approve_all(include_unreviewed=args.include_unreviewed)

    print(changeset.render())
    if args.approve or args.reject or args.approve_all:
        changeset.save(path)
        print()
        print(f"updated {path}")
    return EXIT_OK


def cmd_apply(args) -> int:
    """Apply the approved changes from a reviewed change set."""
    session = Session.open(args.deck, workspace=args.workspace)
    try:
        changeset = ChangeSet.from_json(Path(args.changes).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"error: cannot read change set: {exc}", file=sys.stderr)
        return EXIT_ERROR

    # A change set describes one specific version. Applying it to a different
    # one would fail later with a confusing "target not found"; say so now.
    if changeset.deck and Path(changeset.deck) != session.current.path:
        print(f"error: this change set was built against {Path(changeset.deck).name}, "
              f"but the session is on {session.current.path.name}", file=sys.stderr)
        print("       re-propose against the current version", file=sys.stderr)
        return EXIT_ERROR

    if not changeset.approved:
        print("error: nothing is approved; run slide-wright review first",
              file=sys.stderr)
        print(changeset.render(), file=sys.stderr)
        return EXIT_FINDINGS

    session.changeset = changeset
    try:
        report = session.apply(args.message or "")
    except (SessionError, ApplyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    print(report.render())
    if not report.deliverable:
        print("not delivered - verification failed", file=sys.stderr)
        return EXIT_FINDINGS
    if args.output:
        print()
        print(f"wrote {session.export(args.output)}")
    return EXIT_OK


def cmd_tidy(args) -> int:
    """Clean up an inherited deck in one pass, and prove the content survived.

    The workflow this serves is the ordinary one: a deck assembled from other
    decks, carrying their typefaces and their almost-but-not-quite alignment.
    Doing that as three separate commands means three files, three
    verifications and three places to lose track of what changed.

    So it is one session, one change set, one verification and one point to
    revert to. Every change is presentational by construction, and the promise
    is the same as always, inverted: *change what does not conform, change not
    one word or number, and prove it.*

    With no template given, the deck's own theme is the authority. That is the
    right default here rather than a fallback -- a deck assembled from several
    sources has a visual system of its own, and the pasted-in slides are the
    ones that depart from it.
    """
    from slide_wright.audit import audit as audit_deck
    from slide_wright.brand import TemplateError, plan_conformance, read_profile
    from slide_wright.diff import diff as deck_diff
    from slide_wright.inspect import EMU_PER_INCH
    from slide_wright.layout import plan_alignment

    session = Session.open(args.deck, workspace=args.workspace)
    deck = session.deck()
    name = Path(args.deck).name

    # What looks like it came from somewhere else. Reported, never acted on:
    # naming a slide foreign is a judgement, and the corrections below stand on
    # their own without it.
    findings = [
        o for o in audit_deck(deck, name).observations
        if o.area.value == "consistency" and o.slides
    ]
    if findings:
        print("SLIDES WORTH A LOOK")
        print()
        for observation in findings:
            print(f"  · slides {', '.join(str(n) for n in observation.slides)} — "
                  f"{observation.message}")
        print()

    try:
        profile = read_profile(args.template or args.deck)
    except (TemplateError, UnsafePackageError) as exc:
        # A damaged template used to raise lxml's own error straight out of
        # `main()`, which prints a traceback at whoever ran the command. The
        # API had wrapped it and the CLI had not, so the message you got
        # depended on which surface you came through -- which is not a message.
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    conformance = plan_conformance(deck, profile, name)
    alignment = plan_alignment(deck, int(round(args.tolerance * EMU_PER_INCH)), name)

    print(f"TIDY — {name}")
    print()
    print(f"  {len(conformance.changes)} typeface(s) off the "
          f"{'template' if args.template else 'deck theme'}")
    print(f"  {len(alignment.changes)} shape(s) nearly, but not quite, aligned")
    print()

    if not conformance.changes and not alignment.changes:
        print("  Nothing to tidy.")
        return EXIT_OK
    if args.dry_run:
        print("dry run — nothing was applied")
        return EXIT_OK

    changeset = session.propose(f"tidy {name}")
    for lock in args.lock or []:
        scope, _, target = lock.partition(":")
        try:
            changeset.lock(scope, target)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_ERROR
    for change in [*conformance.changes, *alignment.changes]:
        changeset.add(change)
    changeset.approve_all()

    if not changeset.approved:
        print("every change was blocked by a lock; nothing to apply", file=sys.stderr)
        return EXIT_FINDINGS

    before = session.current.path
    try:
        report = session.apply(f"tidy {name}")
    except (SessionError, ApplyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    content = deck_diff(before, session.current.path).content_deltas
    if content:
        print("REFUSED — tidying changed content:", file=sys.stderr)
        for delta in content[:5]:
            print(f"    · slide {delta.slide} — {delta.description}", file=sys.stderr)
        return EXIT_ERROR

    print(report.render())
    print()
    typefaces = sum(1 for c in changeset.applied if c.op is Op.SET_FONT)
    nudges = sum(1 for c in changeset.applied if c.op is Op.MOVE)
    print(f"  {typefaces} typeface(s) conformed · {nudges} shape(s) nudged · "
          f"0 words or numbers changed, verified")
    print(f"  revert with: slide-wright revert {args.deck} --to "
          f"{session.current.number - 1}")
    if args.output:
        print(f"wrote {session.export(args.output)}")
    return EXIT_OK


def cmd_align(args) -> int:
    """Find shapes that are nearly aligned, and optionally snap them.

    Deliberately conservative: a shape is only touched when its edge already
    sits within the tolerance of a group's, so nothing can move further than
    the distance that made it a near-miss. A deliberate offset never joins a
    group and is never seen by this at all.
    """
    from slide_wright.diff import diff as deck_diff
    from slide_wright.inspect import EMU_PER_INCH
    from slide_wright.layout import plan_alignment

    tolerance = int(round(args.tolerance * EMU_PER_INCH))
    session = Session.open(args.deck, workspace=args.workspace)
    plan = plan_alignment(session.deck(), tolerance, Path(args.deck).name)
    print(plan.render())
    print()

    if plan.empty:
        return EXIT_OK
    if not args.fix:
        print("  run again with --fix to apply these")
        return EXIT_FINDINGS

    changeset = session.propose("align near-miss edges")
    for lock in args.lock or []:
        scope, _, target = lock.partition(":")
        try:
            changeset.lock(scope, target)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_ERROR
    for change in plan.changes:
        changeset.add(change)
    changeset.approve_all()

    if not changeset.approved:
        print("every nudge was blocked by a lock; nothing to apply", file=sys.stderr)
        return EXIT_FINDINGS

    before = session.current.path
    try:
        report = session.apply("align near-miss edges")
    except (SessionError, ApplyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    # Same guarantee as the conformance pass: geometry may change, content
    # never. Checked, because a layout pass that reflowed a number would be
    # the worst kind of quiet failure.
    content = deck_diff(before, session.current.path).content_deltas
    if content:
        print("REFUSED — an alignment pass changed content:", file=sys.stderr)
        for delta in content[:5]:
            print(f"    · slide {delta.slide} — {delta.description}", file=sys.stderr)
        return EXIT_ERROR

    print(report.render())
    print()
    print(f"  {len(changeset.applied)} shape(s) nudged · "
          f"0 words or numbers changed, verified")
    if args.output:
        print(f"wrote {session.export(args.output)}")
    return EXIT_OK


def cmd_history(args) -> int:
    """Every version of this deck, and which one is current."""
    session = Session.open(args.deck, workspace=args.workspace)
    print(session.history())
    return EXIT_OK


def cmd_revert(args) -> int:
    """Go back to an earlier version.

    Nothing is undone and nothing is deleted -- an earlier version is simply
    made current again. The discarded artifacts stay in the workspace.
    """
    session = Session.open(args.deck, workspace=args.workspace)
    try:
        version = session.rollback(args.to)
    except SessionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    print(f"now at v{version.number:03d}"
          + (f" — {version.note}" if version.note else ""))
    print()
    print(session.history())
    if args.output:
        print(f"\nwrote {session.export(args.output)}")
    return EXIT_OK


def cmd_diff(args) -> int:
    """What changed between two decks, structurally.

    Complements `verify`, which answers whether the package is intact. This
    answers what a reader would notice.
    """
    from slide_wright.diff import diff as deck_diff

    result = deck_diff(args.source, args.output)
    print(result.render(limit=args.limit))
    return EXIT_FINDINGS if result.changed else EXIT_OK


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
    p.add_argument("--fix", action="store_true",
                   help="correct off-template typefaces, changing no content")
    p.add_argument("--lock", action="append", metavar="SCOPE[:TARGET]")
    p.add_argument("-o", "--output", help="write the corrected deck here")
    p.add_argument("--workspace", help="where versions are kept")
    p.add_argument("--dry-run", action="store_true", help="show the plan and stop")
    p.set_defaults(func=cmd_brand)

    p = sub.add_parser("verify", help="compare an output against its source")
    p.add_argument("source")
    p.add_argument("output")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("propose", help="work out changes and write them down, applying nothing")
    p.add_argument("deck")
    p.add_argument("--set", action="append", metavar="SLIDE:TARGET:BEFORE=AFTER",
                   help="a change; repeatable")
    p.add_argument("--instruct", metavar="TEXT",
                   help="describe the change in plain language (needs a model key)")
    p.add_argument("--lock", action="append", metavar="SCOPE[:TARGET]",
                   help="protect content: numbers, wording, layout, tables, "
                        "charts, media, slide:4, shape:7")
    p.add_argument("-o", "--output", help="where to write the change set")
    p.add_argument("-m", "--message", help="what this edit is for")
    p.add_argument("--workspace", help="where versions are kept")
    p.set_defaults(func=cmd_propose)

    p = sub.add_parser("review", help="approve or reject individual changes")
    p.add_argument("changes", help="a change set written by propose")
    p.add_argument("--approve", action="append", metavar="ID",
                   help="approve one change; repeatable")
    p.add_argument("--reject", action="append", metavar="ID",
                   help="reject one change; repeatable")
    p.add_argument("--approve-all", action="store_true",
                   help="approve everything still proposed")
    p.add_argument("--include-unreviewed", action="store_true",
                   help="with --approve-all, also approve model changes that "
                        "carry no citation")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("apply", help="apply the approved changes from a change set")
    p.add_argument("deck")
    p.add_argument("changes")
    p.add_argument("-o", "--output", help="write the verified deck here")
    p.add_argument("-m", "--message", help="what this edit is for")
    p.add_argument("--workspace", help="where versions are kept")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("diff", help="what changed between two decks, in words")
    p.add_argument("source")
    p.add_argument("output")
    p.add_argument("--limit", type=int, default=40,
                   help="maximum differences to list (default 40)")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser(
        "tidy",
        help="conform typefaces and snap near-miss alignment in one pass",
    )
    p.add_argument("deck")
    p.add_argument("--template", help=".potx whose theme is the authority; "
                                     "defaults to the deck's own theme")
    p.add_argument("--tolerance", type=float, default=0.02, metavar="INCHES",
                   help="how far out of line still counts as a slip (default 0.02)")
    p.add_argument("--lock", action="append", metavar="SCOPE[:TARGET]")
    p.add_argument("-o", "--output", help="write the tidied deck here")
    p.add_argument("--workspace", help="where versions are kept")
    p.add_argument("--dry-run", action="store_true", help="show the plan and stop")
    p.set_defaults(func=cmd_tidy)

    p = sub.add_parser("align", help="find shapes that are nearly, but not quite, aligned")
    p.add_argument("deck")
    p.add_argument("--fix", action="store_true", help="snap them; changes no content")
    p.add_argument("--tolerance", type=float, default=0.02, metavar="INCHES",
                   help="how far out of line still counts as a slip (default 0.02); "
                        "nothing ever moves further than this")
    p.add_argument("--lock", action="append", metavar="SCOPE[:TARGET]")
    p.add_argument("-o", "--output", help="write the corrected deck here")
    p.add_argument("--workspace", help="where versions are kept")
    p.set_defaults(func=cmd_align)

    p = sub.add_parser("history", help="every version of this deck")
    p.add_argument("deck")
    p.add_argument("--workspace", help="where versions are kept")
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("revert", help="make an earlier version current again")
    p.add_argument("deck")
    p.add_argument("--to", type=int, default=0, metavar="N",
                   help="version number to return to (default 0, the original)")
    p.add_argument("-o", "--output", help="write that version here")
    p.add_argument("--workspace", help="where versions are kept")
    p.set_defaults(func=cmd_revert)

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
