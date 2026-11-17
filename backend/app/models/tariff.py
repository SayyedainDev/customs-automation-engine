"""Structured Pakistan Customs Tariff rows for exact/prefix HS-code lookup.

docs/CUSTOMS_CHATBOT_ARCHITECTURE.md section 7.5: an HS-code duty question
must never be answered by RAG over a chunked tariff PDF - the table
structure a chunk-and-retrieve pipeline destroys is exactly the structure
that carries the meaning. ``scripts/parse_tariff_pdf.py`` parses the source
PDF once into this shape; ``services/customs_chat/knowledge/tariff_lookup.py``
queries it directly, never through the RAG retriever.

Only ``customs_duty_pct`` and ``source_page`` are actually populated by the
current parser - it reads the one rate column this specific tariff PDF
prints per line item. ``additional_duty_pct``, ``regulatory_duty_pct``,
``sales_tax_pct`` and ``income_tax_pct`` are modelled now (matching the
architecture doc's target schema) but stay NULL until the Sales Tax Act,
Income Tax Ordinance and regulatory-duty SROs listed in that doc's section
2.4 are ingested as separate sources - this table must not be read as
already covering them.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TariffLine(Base):
    __tablename__ = "tariff_lines"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    #: Eight digits, no separator - the normalized form (matches
    #: ``services/assistant/foundation.normalize_pct_code``).
    hs_code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text)
    chapter: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    heading: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)

    customs_duty_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    additional_duty_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    regulatory_duty_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    sales_tax_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    income_tax_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tariff_year: Mapped[str] = mapped_column(String(16))
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: False when the parser could not confidently locate this row's rate -
    #: the row is kept (the code and description are still useful) but the
    #: lookup tool must never state ``customs_duty_pct`` from a row with
    #: ``needs_review=True`` as a confirmed fact. See the parser script.
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)

    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
