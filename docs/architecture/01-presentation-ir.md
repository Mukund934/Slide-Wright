# Presentation Intermediate Representation

**Status:** Proposed · **Updated:** 2026-09-04

## Why an IR at all

Raw OOXML is the wrong substrate for reasoning: verbose, deeply nested, and semantically opaque — a title and a footnote are both `<p:sp>`. But abandoning OOXML loses fidelity, which is the entire product.

So: **OOXML for truth, IR for reasoning, with a hash bridge between them.**

| Layer | Role |
|---|---|
| OOXML source | Immutable ground truth. Never edited in place. |
| **IR** | What the model reads and writes. Semantic, positional, provenanced. |
| Rendered preview | What a vision model *looks* at. Never authoritative. |

## Prior art we adopt

The upstream engine already ships a good IR (`ppt-master.svg-authoring-ir.v1`), observed directly in a round-trip:

```xml
<g id="shape-3" data-pptx-object="graphic-frame"
   data-pptx-native-authority="json"     ← JSON wins over the visual
   data-pptx-source-ref="slide:3"         ← provenance to the source file
   data-pptx-semantic-object="table">
  <metadata type="application/json">
   {"schema":"ppt-master.semantic-table.v2","strict_grid":true,"header_rows":1,
    "column_widths":[256,256,256],"rows":[[{"text":"Quarter"},…]]}
  </metadata>
  <image href="…preview.svg" data-pptx-part="authoring-preview"/>   ← not authoritative
</g>
```

Four ideas taken wholesale:

1. **SVG carries geometry; JSON carries semantics; an explicit attribute declares which wins.** A model reasons about position visually and meaning structurally without the two contradicting.
2. **A preview image marked non-authoritative** — a multimodal model can *see* an object while editing its *data*.
3. **Per-object provenance** back to the source file.
4. **Per-subtree content hashes** — the mechanism behind byte-identical passthrough.

## What we add

The upstream IR describes *what a slide contains*. It does not describe *what a slide is for*. Editing and critique both need the second.

| Field | Purpose |
|---|---|
| `message` | The single sentence this slide asserts. Without it, "is this slide working?" is unanswerable. |
| `role` | `cover · agenda · argument · evidence · transition · appendix` — enables whole-deck reasoning |
| `evidence[]` | Citations: source document + locator + extracted value. Kills fabricated numbers. |
| `fidelity_class` | `frozen · editable · generated` — user-declared immutability, enforced by the verifier |
| `measured_bounds` | Post-text-measurement geometry, so overflow is checkable *before* render |
| `change_intent` | Which change-set entry, if any, targets this object |

## Shape (illustrative, not final)

```jsonc
{
  "deck": {
    "source_hash": "sha256:…",              // immutable original
    "theme": { "fonts": {…}, "colors": {…} },
    "masters": […], "layouts": […],
    "sections": [ { "title": "Valuation", "slides": [11,12,13] } ]
  },
  "slides": [{
    "index": 12,
    "source_ref": "slide:12",
    "content_hash": "sha256:…",             // unchanged ⇒ byte passthrough
    "role": "evidence",
    "message": "Trading comps support a 9-11x EV/EBITDA range",
    "fidelity_class": "editable",
    "objects": [{
      "id": "obj-12-4",
      "source_ref": "slide:12/sp:4",
      "content_hash": "sha256:…",
      "semantic": "table",
      "native_authority": "json",
      "frame": { "x": 96, "y": 192, "w": 768, "h": 192 },
      "measured_bounds": { "x": 96, "y": 192, "w": 764, "h": 188, "overflow": false },
      "fidelity_class": "frozen",           // user locked this table
      "data": { "schema": "semantic-table.v2", "rows": [ … ] },
      "evidence": [{
        "source": "comps_model.xlsx", "locator": "Comps!D14",
        "value": "9.4x", "extracted_at": "2026-09-04T10:22:00Z"
      }],
      "preview": "previews/obj-12-4.svg"    // non-authoritative
    }]
  }]
}
```

## Invariants

1. **Every object has a stable id and a content hash.** No exceptions — this is what makes verification arithmetic.
2. **Every object that came from the source has a `source_ref`.** Generated objects are explicitly marked as such.
3. **Semantic data is authoritative over the visual.** Where both exist, `native_authority` decides.
4. **Previews are never inputs to correctness.** A vision model may advise from them; it may not decide.
5. **Numeric values carry evidence or are marked unsourced.** An unsourced number is surfaced to the user, not silently presented.
6. **`frozen` is enforced at the verifier, not just the planner.** A guarantee checked only at intent is not a guarantee.

## Deliberately deferred

- **Animation and transition modelling** — richly supported upstream, irrelevant to the editing wedge.
- **Full SmartArt semantics** — will likely be treated as an atomic preserved object at first. Whether that is sufficient is Phase-0 experiment 1 (R2).
- **Cross-deck concepts** (brand memory, reusable components) — V3 territory.
