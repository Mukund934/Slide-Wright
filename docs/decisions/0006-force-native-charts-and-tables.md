# ADR-0006 — Force native charts and tables on every export

Status:  accepted
Date:    2026-09-04

## Context

A defect verified by direct measurement, 4 Sep 2026.

Exporting an edited deck **without** `--native-charts-and-tables` silently converts native tables into pictures **and discards the edits**, while reporting `status=passed-with-warnings`:

```
native <a:tbl>   before=1  after=0     table destroyed
pictures         before=0  after=1     replaced by an image
edits present:   False                 silently lost
```

Neither emitted warning mentioned the loss. With the flag set, the same edit preserves the native table and lands both values correctly.

This is the most dangerous behaviour in the system: a customer would receive a board deck whose figures are a **picture of the previous quarter's numbers**, with no indication anything went wrong.

## Decision

1. Force `--native-charts-and-tables` on every export path. **No configuration option to disable it.**
2. Assert post-export that native table counts and chart part counts are **≥ source**.
3. Fail the job loudly on any shortfall. Do not deliver.

## Alternatives

| Option | Rejected because |
|---|---|
| Default on, allow override | There is no legitimate reason a user would want silent numeric data loss |
| Warn instead of fail | A warning in a log does not stop a corrupted deck reaching a board |
| Patch upstream and depend on the fix | Worth contributing, but our guarantee cannot wait on someone else's release |

## Consequences

- Trivial implementation cost: one flag, three assertions.
- The assertion belongs in the verifier, so it is covered by the same machinery as change attribution.
- Worth reporting upstream as a bug — the intermediate representation carries the table data correctly, so the defect is confined to the export path.

## Reversal

If upstream changes the default and the failure mode is removed, the forced flag becomes redundant. **Keep the assertions regardless** — they are cheap and they verify the property we actually promise.
