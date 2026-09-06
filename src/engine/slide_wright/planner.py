"""Turn an instruction into a change set.

This is the one place a model is allowed to influence the deck, and its output
is constrained twice over:

  · it may only emit operations the applier can perform surgically;
  · every change it proposes is validated against the deck's actual structure
    before it enters the change set. A change naming a shape that does not
    exist is dropped here, not discovered at apply time.

The model never sees or writes OOXML. It sees a compact structural summary and
returns JSON. Raw model text never reaches a file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from slide_wright.changeset import Change, ChangeSet, Op
from slide_wright.inspect import DeckInfo
from slide_wright.llm.client import Budget, Completion, Provider, StubProvider
from slide_wright.llm.usage import Ledger

SYSTEM = """You edit PowerPoint decks. You are given the structure of a deck and an \
instruction, and you return the smallest set of changes that satisfies it.

Rules you must follow:
- Change only what the instruction asks for. Never propose an improvement that \
was not requested.
- Emit only these ops: set_text, set_table_cell, set_font_size, move, resize.
- Every change must name a shape id that appears in the deck summary.
- "before" must be the text currently in that shape, exactly.
- Table cells are addressed as "<shape_id>/r<row>/c<col>", zero-indexed.
- If the instruction cannot be satisfied with these ops, return an empty list.

Return JSON only: a list of objects with keys op, slide, target, before, after, \
rationale. No prose, no code fence."""


@dataclass
class PlanResult:
    changeset: ChangeSet
    provider: str
    completion: Completion | None = None
    dropped: list[tuple[dict, str]] = field(default_factory=list)

    @property
    def is_stub(self) -> bool:
        """True when no real model produced this. Never let a stub pass as real."""
        return self.provider == "stub"


def summarise(deck: DeckInfo, max_text: int = 70) -> str:
    """A compact structural view for the model.

    Only what is needed to address an object: id, kind, and current text. No
    geometry noise, no XML, and nothing from parts the model has no business
    seeing.
    """
    lines = [f"deck: {deck.slide_count} slides"]
    for slide in deck.slides:
        lines.append(f"slide {slide.number}: {slide.title or '(untitled)'}")
        for shape in slide.shapes:
            desc = f"  id={shape.id} kind={shape.kind}"
            if shape.kind == "table":
                desc += f" rows={shape.table_rows} cols={shape.table_cols}"
            if shape.has_text:
                text = shape.text.replace("\n", " ")
                desc += f' text="{text[:max_text]}"'
            lines.append(desc)
    return "\n".join(lines)


def plan(
    deck: DeckInfo,
    instruction: str,
    *,
    deck_path: str = "",
    provider: Provider | None = None,
    budget: Budget | None = None,
    ledger: Ledger | None = None,
) -> PlanResult:
    """Ask a provider for a change set, then validate it against the deck."""
    provider = provider or StubProvider()
    budget = budget or Budget()

    try:
        completion = provider.complete(
            SYSTEM, f"{summarise(deck)}\n\ninstruction: {instruction}"
        )
    except Exception as exc:
        # A failed call still costs quota and still tells us something.
        if ledger is not None:
            ledger.record("plan", provider.name, deck=deck_path, ok=False, error=str(exc))
        raise
    budget.charge(completion.usage)
    if ledger is not None:
        ledger.record("plan", provider.name, completion, deck=deck_path)

    changeset = ChangeSet(deck=deck_path, instruction=instruction)
    result = PlanResult(changeset=changeset, provider=provider.name, completion=completion)

    try:
        proposed = completion.json()
    except (json.JSONDecodeError, ValueError) as exc:
        result.dropped.append(({}, f"model returned invalid JSON: {exc}"))
        return result

    if not isinstance(proposed, list):
        result.dropped.append(({}, "model did not return a list"))
        return result

    for i, raw in enumerate(proposed, start=1):
        change, reason = _validate(raw, deck, index=i)
        if change is None:
            result.dropped.append((raw if isinstance(raw, dict) else {}, reason))
            continue
        changeset.add(change)

    return result


def _validate(raw, deck: DeckInfo, index: int) -> tuple[Change | None, str]:
    """Reject anything that does not correspond to the real deck."""
    if not isinstance(raw, dict):
        return None, "not an object"

    try:
        op = Op(raw.get("op", ""))
    except ValueError:
        return None, f"unknown op {raw.get('op')!r}"

    from slide_wright.apply import IN_PLACE_OPS

    if op not in IN_PLACE_OPS:
        return None, f"op {op.value} cannot be applied surgically"

    slide_no = raw.get("slide")
    if not isinstance(slide_no, int):
        return None, "slide must be an integer"
    slide = deck.slide(slide_no)
    if slide is None:
        return None, f"slide {slide_no} does not exist"

    target = str(raw.get("target", ""))
    shape_id = target.split("/")[0]
    shape = next((s for s in slide.shapes if s.id == shape_id), None)
    if shape is None:
        return None, f"shape {shape_id!r} is not on slide {slide_no}"

    if op is Op.SET_TABLE_CELL:
        if shape.kind != "table":
            return None, f"shape {shape_id} is not a table"
        if "/r" not in target or "/c" not in target:
            return None, "table target must be <id>/r<row>/c<col>"

    if op is Op.SET_TEXT:
        before = str(raw.get("before", ""))
        if before and before not in shape.text:
            return None, f"before text is not present in shape {shape_id}"

    return (
        Change(
            id=raw.get("id") or f"c{index}",
            op=op,
            slide=slide_no,
            target=target,
            before=raw.get("before"),
            after=raw.get("after"),
            rationale=str(raw.get("rationale", "")),
        ),
        "",
    )
