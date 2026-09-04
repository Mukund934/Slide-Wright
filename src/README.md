# src/

Implementation. Empty during Phase 0 by design — see `docs/architecture/`.

Planned layout when Phase 1 begins:

```
engine/     Python. Ingest, IR, gate, render, verify. The only code that touches deck bytes.
api/        TypeScript. Stateless; enqueues jobs.
web/        TypeScript. The deck-centred interface.
```

See ADR-0005 for the language split and the queue boundary.
