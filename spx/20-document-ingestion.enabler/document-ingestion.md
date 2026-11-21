---
id: 019ab100-0000-7000-8000-000000000020
---

# Document Ingestion and Provenance Preview

PROVIDES validated export document storage, PDF text/coordinate persistence, and on-demand page image previews with value bounding boxes
SO THAT downstream extraction pipelines and human reviewers
CAN inspect authoritative document bytes and verify visual field provenance against source pages.

Governs storage and page-preview conformance under [18-architecture-and-evidence.adr.md](spx/18-architecture-and-evidence.adr.md).

## Assertions

### Conformance

- Valid PDF and OOXML DOCX uploads matching allowed media extensions and magic bytes persist metadata and return `201 Created` ([test](tests/test_upload_and_preview.conformance.l1.py))
- Spoofed PDF headers, empty files, and unsupported media types or extensions fail closed with `400 Bad Request` or `415 Unsupported Media Type` ([test](tests/test_upload_and_preview.conformance.l1.py))
- Page preview requests on stored PDFs render PNG images and locate bounding-box highlights for extracted values, returning `404` or `422` when a document, page, or PDF stream is invalid ([test](tests/test_upload_and_preview.conformance.l1.py))
