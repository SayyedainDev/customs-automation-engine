"""Conformance evidence for the extraction benchmark CLI and deterministic scoring."""

from __future__ import annotations

from scripts import benchmark_extraction
from cace_testing.harnesses.fixtures_paths import synthetic_factory_dir


def test_benchmark_parser_supports_text_and_scanned_options() -> None:
    parser = benchmark_extraction.build_arg_parser()
    args = parser.parse_args(
        [
            "--variant",
            "both",
            "--limit",
            "4",
            "--scenario",
            "clean_bedsheets",
            "--estimate-tokens",
            "--dry-run",
            "--verify-offline",
            "--delay-seconds",
            "1.5",
        ]
    )
    assert args.variant == "both"
    assert args.limit == 4
    assert args.scenario == "clean_bedsheets"
    assert args.estimate_tokens is True
    assert args.dry_run is True
    assert args.verify_offline is True
    assert args.delay_seconds == 1.5


def test_benchmark_runs_offline_deterministic_pass_over_synthetic_factory() -> None:
    factory_root = synthetic_factory_dir()
    documents = benchmark_extraction.load_benchmark_documents(
        factory_root=factory_root,
        variant="text",
        limit=2,
        scenario_filter="clean_bedsheets",
    )
    assert len(documents) == 2
    assert {doc.document_type for doc in documents} == {
        "commercial_invoice",
        "packing_list",
    }

    estimate = benchmark_extraction.estimate_token_budget(documents)
    assert estimate["documents"] == 2
    assert estimate["A_calls"] == 2

    results_b = [benchmark_extraction.run_approach_b(doc) for doc in documents]
    summary = benchmark_extraction.summarize_approach("B_deterministic_only", results_b)
    assert summary.documents_count == 2
    assert summary.total_tokens == 0
    assert summary.total_fields > 0
    assert 0.0 <= summary.accuracy <= 1.0

    scanned_docs = benchmark_extraction.load_benchmark_documents(
        factory_root=factory_root,
        variant="scanned",
        limit=2,
        scenario_filter="clean_bedsheets",
    )
    assert len(scanned_docs) == 2
    assert all(doc.variant == "scanned" for doc in scanned_docs)
