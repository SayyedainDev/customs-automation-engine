"""Stage 6 retrieval evaluation over the gold dataset.

Ingests the corpus (idempotent) then scores the hybrid retriever against
``tests/fixtures/regulatory_gold_eval.json`` and writes a metrics report:
Recall@5, Precision@5, MRR, page-reference accuracy, source-document accuracy
and evidence-not-found accuracy.

Run:
    python -m scripts.evaluate_retrieval
"""

from __future__ import annotations

import json
from pathlib import Path

from app.services.regulatory.evidence_api import run_evidence_search
from app.schemas.regulatory_evidence import EvidenceSearchRequest
from app.services.regulatory.index_db import index_session
from app.services.regulatory.ingestion import ingest_all

GOLD_PATH = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "regulatory_gold_eval.json"
)
REPORT_PATH = (
    Path(__file__).resolve().parents[1] / "reports" / "retrieval_evaluation_report.md"
)
TOP_K = 5


def _matches(result, expected_source_any: list[str], expected_pct: str | None) -> bool:
    if any(sub.lower() in result.source_document.lower() for sub in expected_source_any):
        return True
    return False


def evaluate(db) -> dict:
    gold = json.loads(GOLD_PATH.read_text(encoding="utf-8"))["questions"]
    recall_hits = 0
    recall_total = 0
    precision_sum = 0.0
    precision_total = 0
    mrr_sum = 0.0
    mrr_total = 0
    page_hits = 0
    page_total = 0
    source_top1_hits = 0
    source_top1_total = 0
    enf_hits = 0
    enf_total = 0
    per_question: list[dict] = []

    for q in gold:
        response = run_evidence_search(
            db,
            EvidenceSearchRequest(
                query=q["query"],
                pct_code=q["pct_code"],
                destination_country=q["destination_country"],
                top_k=TOP_K,
                verified_only=True,
            ),
        )
        results = response.results
        row = {"id": q["id"], "category": q["category"], "status": response.status}

        if not q["evidence_should_exist"]:
            enf_total += 1
            correct = response.status == "evidence_not_found"
            enf_hits += int(correct)
            row["evidence_not_found_correct"] = correct
            per_question.append(row)
            continue

        expected_any = q["expected_source_any"]
        expected_pct = q["expected_pct"]

        # Recall@5: any relevant doc in top-5.
        recall_total += 1
        ranks = [
            i + 1
            for i, r in enumerate(results)
            if _matches(r, expected_any, expected_pct)
        ]
        hit = bool(ranks)
        recall_hits += int(hit)
        row["recall_hit"] = hit

        # Precision@5.
        if results:
            relevant = sum(1 for r in results if _matches(r, expected_any, expected_pct))
            precision_sum += relevant / len(results)
            precision_total += 1

        # MRR.
        mrr_total += 1
        mrr_sum += (1.0 / ranks[0]) if ranks else 0.0

        # Source-document top-1 accuracy.
        source_top1_total += 1
        if results and _matches(results[0], expected_any, expected_pct):
            source_top1_hits += 1

        # Page-reference accuracy (only where a page is expected).
        if q.get("expected_page") is not None:
            page_total += 1
            if any(r.page_number == q["expected_page"] for r in results):
                page_hits += 1

        row["top_source"] = results[0].source_document if results else None
        per_question.append(row)

    def ratio(a: int, b: int) -> float:
        return round(a / b, 4) if b else 0.0

    return {
        "recall_at_5": ratio(recall_hits, recall_total),
        "precision_at_5": round(precision_sum / precision_total, 4) if precision_total else 0.0,
        "mrr": round(mrr_sum / mrr_total, 4) if mrr_total else 0.0,
        "page_reference_accuracy": ratio(page_hits, page_total),
        "source_document_accuracy": ratio(source_top1_hits, source_top1_total),
        "evidence_not_found_accuracy": ratio(enf_hits, enf_total),
        "questions_evaluated": len(gold),
        "per_question": per_question,
    }


def render(metrics: dict) -> str:
    lines = ["# Stage 6 — Retrieval Evaluation Report", ""]
    lines.append("Gold set: `tests/fixtures/regulatory_gold_eval.json`. Top-k = 5. "
                 "verified_only = true.")
    lines.append("")
    lines.append("## Metrics")
    lines.append("")
    for key in (
        "recall_at_5",
        "precision_at_5",
        "mrr",
        "page_reference_accuracy",
        "source_document_accuracy",
        "evidence_not_found_accuracy",
        "questions_evaluated",
    ):
        lines.append(f"- **{key}**: {metrics[key]}")
    lines.append("")
    lines.append("## Per-question")
    lines.append("")
    lines.append("| id | category | status | hit | top source |")
    lines.append("|---|---|---|---|---|")
    for row in metrics["per_question"]:
        hit = row.get("recall_hit", row.get("evidence_not_found_correct", ""))
        lines.append(
            f"| {row['id']} | {row['category']} | {row['status']} | {hit} | "
            f"{(row.get('top_source') or '')[:44]} |"
        )
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- The vector stage is a TF-IDF cosine (offline stand-in for a dense "
                 "sentence-transformer); the reranker is a lexical scorer (offline "
                 "stand-in for a transformer cross-encoder). BM25, RRF and parent "
                 "retrieval are full implementations.")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    db = index_session()
    try:
        ingest_all(db)  # idempotent
        metrics = evaluate(db)
    finally:
        db.close()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(render(metrics), encoding="utf-8")
    print(f"Wrote {REPORT_PATH}")
    for key in (
        "recall_at_5",
        "precision_at_5",
        "mrr",
        "page_reference_accuracy",
        "source_document_accuracy",
        "evidence_not_found_accuracy",
    ):
        print(f"  {key}: {metrics[key]}")


if __name__ == "__main__":
    main()
