"""Tests for the structured tariff lookup tool (docs section 7.5): the pure
text parser, and the exact/prefix lookup against seeded ``tariff_lines``
rows. No PDF or live Postgres is touched - the parser tests feed it plain
strings shaped like the pages already inspected in the real PDF, and the
lookup tests use the suite's standard in-memory SQLite fixture."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.tariff import TariffLine
from app.services.customs_chat.knowledge import tariff_lookup
from app.services.customs_chat.knowledge.tariff_parser import parse_tariff_page

SAMPLE_PAGE = """
49.05
Maps and hydrographic or similar charts of all kinds,
including atlases, wall maps, topographical plans and
globes, printed.
4905.2000
- In book form
0
4905.9000
- Other
0
49.07
Unused postage, revenue or similar stamps of current
or new issue.
4907.0090
- - - Other
15
"""


def test_parser_extracts_codes_descriptions_and_rates():
    rows = parse_tariff_page(SAMPLE_PAGE, page_number=131)
    by_code = {r.hs_code: r for r in rows}
    assert set(by_code) == {"49052000", "49059000", "49070090"}
    assert by_code["49052000"].customs_duty_pct == 0.0
    assert by_code["49070090"].customs_duty_pct == 15.0
    assert by_code["49052000"].description == "In book form"
    assert by_code["49052000"].needs_review is False
    assert by_code["49052000"].source_page == 131


def test_parser_marks_heading_only_lines_as_not_a_row():
    rows = parse_tariff_page(SAMPLE_PAGE, page_number=131)
    codes = {r.hs_code for r in rows}
    assert "4905" not in codes and "4907" not in codes


def test_parser_flags_missing_rate_for_review_instead_of_guessing():
    text = "6203.4200\n- Of cotton\n\n62.04\nWomen's trousers"
    rows = parse_tariff_page(text, page_number=158)
    assert len(rows) == 1
    row = rows[0]
    assert row.hs_code == "62034200"
    assert row.customs_duty_pct is None
    assert row.needs_review is True


def test_parser_ignores_blank_lines_and_whitespace_noise():
    text = "\n\n4906.0000\n\n\nPlans and drawings.\n\n0\n\n"
    rows = parse_tariff_page(text, page_number=131)
    assert len(rows) == 1
    assert rows[0].customs_duty_pct == 0.0


# --- extract_hs_code -------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Is 62034200 dutiable?", "62034200"),
        ("What's the duty on 6203.4200?", "62034200"),
        ("HS code 6203-4200 please", "62034200"),
        ("What is a bill of lading?", None),
    ],
)
def test_extract_hs_code(text, expected):
    assert tariff_lookup.extract_hs_code(text) == expected


# --- lookup_tariff / describe_for_prompt -----------------------------------


@pytest.fixture()
def tariff_engine() -> Generator[Engine, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        session.add(
            TariffLine(
                hs_code="62034200",
                description="Men's or boys' trousers - of cotton.",
                chapter="62",
                heading="6203",
                customs_duty_pct=20.0,
                tariff_year="FY2025-26",
                source_page=158,
                needs_review=False,
            )
        )
        session.add(
            TariffLine(
                hs_code="62034900",
                description="Men's or boys' trousers - of other textile materials.",
                chapter="62",
                heading="6203",
                customs_duty_pct=None,
                tariff_year="FY2025-26",
                source_page=158,
                needs_review=True,
            )
        )
        session.commit()
    yield engine
    engine.dispose()


def _session(engine: Engine) -> Session:
    return Session(engine)


def test_exact_match_returns_confirmed_rate(tariff_engine):
    db = _session(tariff_engine)
    result = tariff_lookup.lookup_tariff(db, "62034200")
    assert result.matched is True
    assert result.exact is True
    assert result.needs_review is False
    assert result.customs_duty_pct == 20.0
    assert "20" in tariff_lookup.describe_for_prompt(result)


def test_exact_match_with_needs_review_never_states_a_confirmed_rate(tariff_engine):
    db = _session(tariff_engine)
    result = tariff_lookup.lookup_tariff(db, "62034900")
    assert result.matched is True
    assert result.needs_review is True
    text = tariff_lookup.describe_for_prompt(result)
    assert "could not be confidently read" in text
    assert "%" not in text


def test_no_match_is_reported_honestly_not_guessed(tariff_engine):
    db = _session(tariff_engine)
    result = tariff_lookup.lookup_tariff(db, "99999999")
    assert result.matched is False
    assert "No tariff entry was found" in tariff_lookup.describe_for_prompt(result)


def test_prefix_search_returns_candidates(tariff_engine):
    db = _session(tariff_engine)
    result = tariff_lookup.lookup_tariff(db, "6203")
    assert result.matched is True
    assert result.exact is False
    assert len(result.candidates) == 2
