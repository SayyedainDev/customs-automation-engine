from functools import lru_cache
from pathlib import Path
from decimal import Decimal
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Enterprise Customs Automation Engine"
    # The Docker image enables this after building the Vite application. It is
    # off by default so backend-only development and tests keep the JSON root.
    serve_frontend: bool = False
    # Enables one-click sample shipment loading from bundled synthetic_factory
    # fixtures for interactive demos and local evaluation.
    enable_demo_samples: bool = True

    database_url: str | None = None
    upload_dir: Path = Path("uploads/documents")
    max_upload_size_mb: int = 10
    groq_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="GROQ_API_KEY",
    )
    groq_model: str = "openai/gpt-oss-20b"
    # The largest current structured-extraction fixture (a five-line invoice)
    # serializes to about 7.5k characters. A 2k completion-token budget leaves
    # headroom for that strict JSON while bounding every extraction request.
    groq_structured_max_completion_tokens: int = Field(
        default=2000,
        ge=256,
        le=8192,
        validation_alias="GROQ_STRUCTURED_MAX_COMPLETION_TOKENS",
    )
    # Supporting-document gap fill returns a flat object containing at most the
    # unresolved important fields, rather than the complete 20-field document
    # schema. 512 tokens is ample for the largest current Form-E/COO gap-fill
    # response while preventing two small documents from reserving the global
    # 2,000-token structured-extraction budget each.
    groq_supporting_gapfill_max_completion_tokens: int = Field(
        default=512,
        ge=128,
        le=1024,
        validation_alias="GROQ_SUPPORTING_GAPFILL_MAX_COMPLETION_TOKENS",
    )
    groq_supporting_gapfill_tpm_budget: int = Field(
        default=8_000,
        ge=1_000,
        le=100_000,
        validation_alias="GROQ_SUPPORTING_GAPFILL_TPM_BUDGET",
    )
    supporting_gapfill_snippets_per_field: int = Field(
        default=2,
        ge=1,
        le=4,
        validation_alias="SUPPORTING_GAPFILL_SNIPPETS_PER_FIELD",
    )
    supporting_gapfill_snippet_characters: int = Field(
        default=500,
        ge=128,
        le=1000,
        validation_alias="SUPPORTING_GAPFILL_SNIPPET_CHARACTERS",
    )
    supporting_gapfill_max_context_characters: int = Field(
        default=2500,
        ge=500,
        le=5000,
        validation_alias="SUPPORTING_GAPFILL_MAX_CONTEXT_CHARACTERS",
    )
    supporting_gapfill_max_pages: int = Field(
        default=3,
        ge=1,
        le=5,
        validation_alias="SUPPORTING_GAPFILL_MAX_PAGES",
    )
    # "hybrid": regex/table extraction first, one combined Groq gap-fill call
    # per document at most, staged per-line ladder unreachable.
    # "legacy": full-document single-shot Groq call, staged ladder fallback on
    # validation failure - kept for comparison/debugging only.
    extraction_mode: Literal["legacy", "hybrid"] = Field(
        default="hybrid",
        validation_alias="EXTRACTION_MODE",
    )
    ocr_executable: str = "tesseract"
    ocr_language: str = "eng"
    ocr_dpi: int = Field(default=300, ge=150, le=600)
    # PSM 4 ("single column of text of variable sizes"), which matches the shape
    # of an invoice/packing list: a label-value header block, a wide sparse
    # table, then a footer block.
    #
    # PSM 6 ("single uniform block") silently discarded product rows whose
    # columns are separated by wide horizontal gaps - measured across all 14
    # scanned fixtures it recovered 52/62 expected tokens versus 62/62 for
    # PSM 3 and PSM 4 - while OCR confidence stayed ~0.94 either way, so the
    # loss was invisible to the confidence gate. PSM 4 is preferred over PSM 3
    # because it keeps a label and its value on one line ("Exporter Acme Ltd"),
    # whereas PSM 3 emits the label column and the value column as separate
    # blocks and forces the extractor to re-pair them positionally.
    ocr_page_segmentation_mode: int = Field(default=4, ge=0, le=13)
    ocr_timeout_seconds: int = Field(default=60, ge=1, le=300)
    ocr_min_confidence: Decimal = Field(
        default=Decimal("0.80"),
        ge=0,
        le=1,
    )

    # --- Phase 3B production RAG: embeddings, reranker, vector store ---
    regulatory_embedding_model: str = Field(
        default="BAAI/bge-small-en-v1.5",
        validation_alias="REGULATORY_EMBEDDING_MODEL",
    )
    regulatory_embedding_batch_size: int = Field(
        default=32,
        ge=1,
        le=512,
        validation_alias="REGULATORY_EMBEDDING_BATCH_SIZE",
    )
    regulatory_embedding_device: str = Field(
        default="cpu",
        validation_alias="REGULATORY_EMBEDDING_DEVICE",
    )
    regulatory_vector_collection: str = Field(
        default="regulatory_child_chunks",
        validation_alias="REGULATORY_VECTOR_COLLECTION",
    )
    regulatory_reranker_model: str = Field(
        default="cross-encoder/ms-marco-MiniLM-L-6-v2",
        validation_alias="REGULATORY_RERANKER_MODEL",
    )
    regulatory_rerank_top_n: int = Field(
        default=25,
        ge=1,
        le=200,
        validation_alias="REGULATORY_RERANK_TOP_N",
    )
    regulatory_rerank_device: str = Field(
        default="cpu",
        validation_alias="REGULATORY_RERANK_DEVICE",
    )
    # When false, never attempt to import/load heavyweight HF models (keeps unit
    # tests and offline runs from downloading models); explicit degraded mode.
    regulatory_enable_real_models: bool = Field(
        default=True,
        validation_alias="REGULATORY_ENABLE_REAL_MODELS",
    )

    # --- LangGraph multi-agent customs-audit orchestration ---
    langgraph_checkpoint_backend: str = Field(
        default="sqlite",  # "postgres" | "sqlite" | "memory"
        validation_alias="LANGGRAPH_CHECKPOINT_BACKEND",
    )
    langgraph_checkpoint_path: str = Field(
        default="customs_audit_checkpoints.sqlite3",
        validation_alias="LANGGRAPH_CHECKPOINT_PATH",
    )
    langgraph_workflow_timeout_seconds: int = Field(
        default=300,
        ge=1,
        validation_alias="LANGGRAPH_WORKFLOW_TIMEOUT_SECONDS",
    )
    langgraph_max_retries: int = Field(
        default=2,
        ge=0,
        le=10,
        validation_alias="LANGGRAPH_MAX_RETRIES",
    )
    # When false, Broker/Auditor use deterministic non-LLM report builders.
    langgraph_enable_live_agents: bool = Field(
        default=False,
        validation_alias="LANGGRAPH_ENABLE_LIVE_AGENTS",
    )
    langgraph_broker_model: str = Field(
        default="",
        validation_alias="LANGGRAPH_BROKER_MODEL",
    )
    langgraph_auditor_model: str = Field(
        default="",
        validation_alias="LANGGRAPH_AUDITOR_MODEL",
    )
    # Unlike broker/auditor narration (LANGGRAPH_ENABLE_LIVE_AGENTS, optional
    # flavor text, off by default), the final-report explanation is a core
    # workflow feature: it is attempted whenever GROQ_API_KEY is configured,
    # with a deterministic template fallback otherwise.
    langgraph_explanation_model: str = Field(
        default="",
        validation_alias="LANGGRAPH_EXPLANATION_MODEL",
    )
    langgraph_human_review_required: bool = Field(
        default=True,
        validation_alias="LANGGRAPH_HUMAN_REVIEW_REQUIRED",
    )
    ocr_confidence_review_threshold: Decimal = Field(
        default=Decimal("0.80"),
        ge=0,
        le=1,
        validation_alias="LANGGRAPH_OCR_REVIEW_THRESHOLD",
    )

    # --- General Pakistan Customs Assistant (services/customs_chat) ---
    # Replaces the textile-only Regulatory Chat. See
    # docs/CUSTOMS_CHATBOT_ARCHITECTURE.md section 8 for the model-routing
    # rationale (fast model for classify/rewrite/verify, strong model to
    # write the answer the user reads).
    customs_chat_fast_model: str = Field(
        default="llama-3.1-8b-instant",
        validation_alias="CUSTOMS_CHAT_FAST_MODEL",
    )
    customs_chat_strong_model: str = Field(
        default="llama-3.3-70b-versatile",
        validation_alias="CUSTOMS_CHAT_STRONG_MODEL",
    )
    customs_chat_max_completion_tokens: int = Field(
        default=800,
        ge=128,
        le=4096,
        validation_alias="CUSTOMS_CHAT_MAX_COMPLETION_TOKENS",
    )
    customs_chat_temperature: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        validation_alias="CUSTOMS_CHAT_TEMPERATURE",
    )
    # Evidence banding thresholds. Architecture doc section 7.1 proposed
    # calibrating these against the cross-encoder score, but that score's
    # scale is not stable across reranker backends (a real cross-encoder
    # emits roughly [-11, 11] logits; the offline LexicalReranker fallback
    # emits token-overlap in roughly [0, 1.5] - see
    # services/regulatory/reranker.py). Banding is therefore done on
    # ``ScoredEvidence.lexical_relevance_score`` instead: the existing,
    # reranker-independent relevance metric retrieval.py already computes
    # and gates on (``MIN_LEXICAL_RELEVANCE = 0.5``), which the comments
    # there report as ~0.75-0.80 for a genuinely relevant chunk and
    # ``MIN_BASE_LEXICAL_OVERLAP = 0.20`` as the noise floor. These defaults
    # reuse those already-validated cuts; recalibrate against the golden set
    # (docs section 14) once one exists, by env var, no code change needed.
    customs_chat_tau_high: float = Field(
        default=0.5,
        ge=0.0,
        validation_alias="CUSTOMS_CHAT_TAU_HIGH",
    )
    customs_chat_tau_low: float = Field(
        default=0.20,
        ge=0.0,
        validation_alias="CUSTOMS_CHAT_TAU_LOW",
    )
    customs_chat_enable_verifier: bool = Field(
        default=True,
        validation_alias="CUSTOMS_CHAT_ENABLE_VERIFIER",
    )
    customs_chat_enable_ring2: bool = Field(
        default=True,
        validation_alias="CUSTOMS_CHAT_ENABLE_RING2",
    )
    customs_chat_enable_llm_scope_classifier: bool = Field(
        default=True,
        validation_alias="CUSTOMS_CHAT_ENABLE_LLM_SCOPE_CLASSIFIER",
    )
    customs_chat_enable_query_rewriter: bool = Field(
        default=True,
        validation_alias="CUSTOMS_CHAT_ENABLE_QUERY_REWRITER",
    )
    customs_chat_history_turns: int = Field(
        default=6,
        ge=0,
        le=20,
        validation_alias="CUSTOMS_CHAT_HISTORY_TURNS",
    )
    customs_chat_prompt_version: str = Field(
        default="v1",
        validation_alias="CUSTOMS_CHAT_PROMPT_VERSION",
    )
    customs_chat_top_k: int = Field(
        default=6,
        ge=1,
        le=20,
        validation_alias="CUSTOMS_CHAT_TOP_K",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="CUSTOMS_",
        extra="ignore",
    )

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
