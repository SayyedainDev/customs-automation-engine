---
id: 019ab100-0000-7000-8000-000000000061
---

# Human-in-the-Loop Review and Reprocessing

PROVIDES resumable LangGraph workflow interrupts, targeted field correction or missing-document submission, additive deterministic recomputation, and retry state management
SO THAT customs reviewers and workflow API callers
CAN pause on uncertain or non-passing shipments, supply verified corrections or uploaded documents, and resume through completion.

Governs fail-closed review state transitions under [spx/15-authoritative-safety.pdr.md](spx/15-authoritative-safety.pdr.md).

## Assertions

### Scenarios

- Given an uploaded commercial invoice and packing list submitted through the customs-audit workflow, when the workflow executes, then persistent workflow status, deterministic verdict, and immutable SHA-256 audit events are recorded ([test](tests/test_hitl_and_reprocessing.scenario.l1.py))
- Given a paused workflow awaiting human review, when a reviewer confirms or corrects an uncertain extraction field or provides a newly uploaded supporting document, then compliance recomputes deterministically while invalid states or missing upload identifiers fail closed ([test](tests/test_hitl_and_reprocessing.scenario.l1.py))
- Given an unknown workflow identifier or disallowed retry transition, when workflow lookup or retry is requested, then typed not-found or state errors fail cleanly ([test](tests/test_hitl_and_reprocessing.scenario.l1.py))
