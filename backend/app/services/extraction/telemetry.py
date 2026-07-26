"""Per-document token accounting for the hybrid extractor.

Exists so a regression in regex coverage shows up as a number rather than as a
surprise quota exhaustion three days later. ``llm_calls`` is the load-bearing
metric: the hybrid extractor is designed for at most one gap-fill call per
document, so any value above one means the retry ladder has reappeared.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: More than this many gap-fill calls for one document indicates the staged
#: retry ladder has come back; it is reported as a defect, not smoothed over.
MAX_EXPECTED_LLM_CALLS_PER_DOCUMENT = 1


@dataclass
class DocumentTelemetry:
    """What one document cost."""

    document_ref: str
    fields_total: int = 0
    fields_from_regex: int = 0
    fields_from_llm: int = 0
    fields_missing: int = 0
    line_items_from_table: int = 0
    line_items_from_llm: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_calls: int = 0
    cache_hit: bool = False
    model_used: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def regex_coverage(self) -> float:
        if not self.fields_total:
            return 0.0
        return self.fields_from_regex / self.fields_total

    @property
    def exceeded_call_budget(self) -> bool:
        return self.llm_calls > MAX_EXPECTED_LLM_CALLS_PER_DOCUMENT

    def to_json(self) -> dict[str, Any]:
        return {
            "document_ref": self.document_ref,
            "fields_total": self.fields_total,
            "fields_from_regex": self.fields_from_regex,
            "fields_from_llm": self.fields_from_llm,
            "fields_missing": self.fields_missing,
            "line_items_from_table": self.line_items_from_table,
            "line_items_from_llm": self.line_items_from_llm,
            "regex_coverage": round(self.regex_coverage, 4),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "llm_calls": self.llm_calls,
            "exceeded_call_budget": self.exceeded_call_budget,
            "cache_hit": self.cache_hit,
            "model_used": self.model_used,
            "notes": self.notes,
        }


@dataclass
class BatchTelemetry:
    """Aggregate across a run."""

    documents: list[DocumentTelemetry] = field(default_factory=list)
    token_budget: int | None = None
    failovers: int = 0

    def add(self, entry: DocumentTelemetry) -> None:
        self.documents.append(entry)

    @property
    def total_tokens(self) -> int:
        return sum(entry.total_tokens for entry in self.documents)

    @property
    def cache_hits(self) -> int:
        return sum(1 for entry in self.documents if entry.cache_hit)

    @property
    def llm_calls(self) -> int:
        return sum(entry.llm_calls for entry in self.documents)

    @property
    def regex_coverage(self) -> float:
        total = sum(entry.fields_total for entry in self.documents)
        if not total:
            return 0.0
        return sum(entry.fields_from_regex for entry in self.documents) / total

    def budget_exceeded_documents(self) -> list[str]:
        return [entry.document_ref for entry in self.documents if entry.exceeded_call_budget]

    def render(self) -> str:
        count = len(self.documents)
        hit_pct = (self.cache_hits / count * 100) if count else 0.0
        budget = f" / {self.token_budget:,}" if self.token_budget else ""
        lines = [
            f"Documents:        {count}",
            f"Cache hits:       {self.cache_hits} ({hit_pct:.0f}%)",
            f"Regex coverage:   {self.regex_coverage * 100:.1f}%   <- target >=80%",
            f"LLM calls:        {self.llm_calls}",
            f"Tokens used:      {self.total_tokens:,}{budget}",
            f"Failovers:        {self.failovers}",
        ]
        over_budget = self.budget_exceeded_documents()
        if over_budget:
            lines.append(
                f"WARNING: {len(over_budget)} document(s) exceeded "
                f"{MAX_EXPECTED_LLM_CALLS_PER_DOCUMENT} gap-fill call: "
                + ", ".join(over_budget[:5])
            )
        return "\n".join(lines)

    def to_json(self) -> dict[str, Any]:
        return {
            "documents": [entry.to_json() for entry in self.documents],
            "totals": {
                "document_count": len(self.documents),
                "cache_hits": self.cache_hits,
                "regex_coverage": round(self.regex_coverage, 4),
                "llm_calls": self.llm_calls,
                "total_tokens": self.total_tokens,
                "token_budget": self.token_budget,
                "failovers": self.failovers,
                "documents_over_call_budget": self.budget_exceeded_documents(),
            },
        }
