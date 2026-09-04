# Development Guide

**Status:** Phase 0 — no application code exists yet. This describes conventions, not a build.

## Naming

The product is **Slide-Wright** — capital S, capital W, hyphenated. That is the only form that appears in UI copy, documentation prose, commit messages, or anything a customer reads.

Machine-readable variants are permitted **only** where an ecosystem cannot accept a hyphen:

| Context | Identifier |
|---|---|
| Repository / directory | `Slide-Wright` |
| npm package, CLI binary, Docker image, URL slug | `slide-wright` |
| Python package / module | `slide_wright` (PEP 8 forbids hyphens in importable names) |
| JS/TS identifiers, JSON keys | `slideWright` |
| Environment variables | `SLIDE_WRIGHT_` prefix |
| Postgres identifiers | `slide_wright` |

These are technical spellings, not brand variants. Never write `SlideWright` unhyphenated as the product name.

## Repository conventions

| Path | Tracked | Contents |
|---|---|---|
| `docs/` | yes | Engineering documentation, safe to share |
| `src/` | yes | Implementation (empty during Phase 0) |
| `tests/` | yes | Suites and the benchmark corpus |
| `scripts/` | yes | Development and operational tooling |
| `private/` | **no** | Project intelligence — never commit |

The split rule: *"if this repo went public tomorrow, would this help a competitor or embarrass us?"* If yes, it belongs in `private/`.

## Git

- Branch: `main`.
- **Commit granularly.** Many small commits, split by file and by concern. Do not squash.
- Author and committer are Mukund Thakur on every commit. **No AI attribution, no `Co-Authored-By`, no generated-by trailers.**
- Secrets never enter Git. Customer decks never enter Git.

## Handling deck files

Deck fixtures are marked binary in `.gitattributes`. Git must never normalise them — the product is about byte fidelity, so a line-ending rewrite would corrupt the very thing under test.

Only decks explicitly cleared for sharing are tracked. Anything from a customer stays out.

## Non-negotiables

Three rules that exist because a measurement forced them:

1. **Force `--native-charts-and-tables` on every export, and assert native-object counts afterwards.** Without it, native tables silently become pictures and edits are discarded (ADR-0006).
2. **The quality gate is deterministic.** No model decides pass/fail (ADR-0003).
3. **The source file is immutable.** Every output is a new artifact verified against it.

## Verifying a deck by hand

Useful during Phase 0, before any tooling exists:

```bash
# part-level fidelity between two decks
python - <<'PY'
import zipfile, hashlib
a = zipfile.ZipFile("source.pptx"); b = zipfile.ZipFile("output.pptx")
same = [n for n in set(a.namelist()) & set(b.namelist())
        if hashlib.sha256(a.read(n)).hexdigest() == hashlib.sha256(b.read(n)).hexdigest()]
print(f"byte-identical: {len(same)}/{len(a.namelist())}")
PY
```

```bash
# editability: live text runs and native objects, not pictures
python -c "
import zipfile,re
x = zipfile.ZipFile('output.pptx').read('ppt/slides/slide1.xml').decode()
print('shapes', x.count('<p:sp>'), 'pictures', x.count('<p:pic>'),
      'tables', x.count('<a:tbl>'), 'runs', len(re.findall(r'<a:t>', x)))"
```
