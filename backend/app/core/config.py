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

    database_url: str | None = None
    upload_dir: Path = Path("uploads/documents")
    max_upload_size_mb: int = 10
    groq_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="GROQ_API_KEY",
    )
    groq_model: str = "openai/gpt-oss-20b"
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
    # capstone feature: it is attempted whenever GROQ_API_KEY is configured,
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
