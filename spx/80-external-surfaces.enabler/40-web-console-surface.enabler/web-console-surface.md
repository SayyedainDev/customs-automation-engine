---
id: 019ab100-0000-7000-8000-000000000083
---

# React and TypeScript Web Console Surface

PROVIDES an interactive React and TypeScript browser interface for multi-line review, visual PDF provenance inspection, customs audit workflows, dossier downloads, and guarded customs chat
SO THAT exporters and customs analysts
CAN operate all CACE workflows in a type-checked production bundle.

Governs typed frontend asset build verification under [spx/18-architecture-and-evidence.adr.md](spx/18-architecture-and-evidence.adr.md).

## Assertions

### Conformance

- Running TypeScript type-checking and Vite production bundling produces an installable `dist/index.html` asset bundle with exit code `0` ([test](tests/test_web_console_build.conformance.l2.py))
