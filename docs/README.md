# Documentation

Engineering documentation, safe to share. Strategy, pricing, customer research and the roadmap live in `private/` and are never committed.

## Architecture

| Doc | Contents |
|---|---|
| [00-system-architecture](architecture/00-system-architecture.md) | The pipeline, the governing principles, where agents are dangerous |
| [01-presentation-ir](architecture/01-presentation-ir.md) | Internal representation, provenance, content hashing |
| [02-safe-editing](architecture/02-safe-editing.md) | **The defining capability** — five preservation guarantees |
| [03-ai-architecture](architecture/03-ai-architecture.md) | Model selection, provider abstraction, cost control |
| [04-infrastructure-and-data](architecture/04-infrastructure-and-data.md) | Backend choice, data model, what is deliberately absent |
| [05-security](architecture/05-security.md) | Tenant isolation, OOXML threats, provider exposure |
| [06-ux-architecture](architecture/06-ux-architecture.md) | The deck is the interface |
| [07-design-language](architecture/07-design-language.md) | One attention colour, and it means *this changed* |
| [08-deployment-models](architecture/08-deployment-models.md) | Local, customer-hosted, vendor-hosted — what each costs the promise |

## Decisions

[ADR index](decisions/README.md) — ten accepted records. Start with [ADR-0002](decisions/0002-editing-before-generation.md) (editing before generation), [ADR-0006](decisions/0006-force-native-charts-and-tables.md) (the defect that must never reach a customer) and [ADR-0008](decisions/0008-local-first-deployment.md) (the document does not leave the machine).

## Guides

| Doc | Contents |
|---|---|
| [development](guides/development.md) | Conventions, non-negotiables, manual verification snippets |
| [testing-strategy](guides/testing-strategy.md) | The fidelity benchmark and corpus requirements |

## Reading order

New to the project: `README.md` → `architecture/00` → `architecture/02` → ADR-0002.
