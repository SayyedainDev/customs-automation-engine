"""Tests for the "section_aware" chunking strategy (docs/CUSTOMS_CHATBOT_
ARCHITECTURE.md section 6.2), built for the Customs Act and Customs Rules.

The synthetic fixtures below are modelled directly on real extracted text
from ``regulatory_data/raw/fbr/customs_act/customs_act_1969_updated_2025.pdf``
(inspected during development), including its characteristic noise: inline
amendment-history citation markers like ``46[26A. Conducting the audit.-``
that share the exact lexical shape of a genuine section heading.
"""

from __future__ import annotations

from app.services.regulatory.chunking import split_by_sequential_numbered_headings

REAL_SHAPED_PAGE_ONE = """
THE CUSTOMS ACT,1969

39

department, company or organization to furnish such information as is held by that
person.

46[26A. Conducting the audit.- (1) The appropriate officer of customs conducting
an audit may require any person to produce records for inspection.

(2) The Board may require in writing any person to provide information.]

47[27. Ban on purchase of duty free imports.- No person shall purchase goods
which have been imported duty free.]
"""

REAL_SHAPED_PAGE_TWO_FOOTNOTE_HEAVY = """
1. Inserted by Finance Act, 1989(V of 1989), S

2. Inserted by Finance Ordinance, 2002 (XXVII of 2002), S

28. Special provisions regarding banking companies.- Notwithstanding anything
contained in any other law, a banking company shall comply with a request from
the appropriate officer.

3. Omitted vide Finance Ordinance, 2000(XXI of 2000), S
"""

# Body-heading shape (period after the number, as in the Act's running
# text) - NOT the table-of-contents listing shape, which has no period
# after the number at all ("211\nMaintenance of record.\n244") and is
# correctly left unmatched since ingestion only processes body pages.
BODY_SHAPED_TAIL = """
211. Maintenance of record.- Every person importing goods shall keep records.

211A. Access to the premises and records by the officers of Customs.- Any
officer authorised may access such premises.

212. Regulation of business in gold, etc.- No person shall deal in gold
except as permitted.

213. Recovery of money upon certain documents.- Any sum payable may be
recovered as arrears of land revenue.

215. Service of order, decision, etc.- Any order made shall be served on
the person affected.
"""


def test_recovers_real_sections_and_skips_amendment_citation_markers():
    # In real ingestion this page's call would be seeded with whatever
    # section number the previous page last accepted (here, simulating a
    # previous page that ended around section 25) - see
    # test_state_threads_across_a_simulated_page_boundary for that threading
    # itself.
    result = split_by_sequential_numbered_headings(
        REAL_SHAPED_PAGE_ONE, label_prefix="Section", start_last_number=25
    )
    labels = [label for label, _ in result.sections]
    assert "Section 26A" in labels
    assert "Section 27" in labels
    # Nothing derived from the "46[" / "47[" amendment citation numbers.
    assert "Section 46" not in labels
    assert "Section 47" not in labels


def test_footnote_verb_titles_are_excluded_even_when_numerically_plausible():
    result = split_by_sequential_numbered_headings(
        REAL_SHAPED_PAGE_TWO_FOOTNOTE_HEAVY, label_prefix="Section", start_last_number=25
    )
    labels = [label for label, _ in result.sections]
    assert labels == ["Section 28"]


def test_section_body_contains_its_own_text_not_the_next_sections():
    result = split_by_sequential_numbered_headings(
        REAL_SHAPED_PAGE_ONE, label_prefix="Section", start_last_number=25
    )
    by_label = dict(result.sections)
    assert "Conducting the audit" in by_label["Section 26A"]
    assert "Ban on purchase" not in by_label["Section 26A"]
    assert "Ban on purchase" in by_label["Section 27"]


def test_state_threads_across_a_simulated_page_boundary():
    """The real bug this guards against: without carrying the running
    section number from one page's call into the next, the monotonic check
    resets to 0 and rejects the first genuine heading on every page after
    the first."""
    first = split_by_sequential_numbered_headings(
        BODY_SHAPED_TAIL, label_prefix="Section", start_last_number=207
    )
    assert [label for label, _ in first.sections] == [
        "Section 211",
        "Section 211A",
        "Section 212",
        "Section 213",
        "Section 215",
    ]
    assert first.last_accepted_number == 215

    continuation_page = "216. No compensation for loss or injury except on proof of neglect.-\nSome body text.\n"
    without_threading = split_by_sequential_numbered_headings(continuation_page, label_prefix="Section")
    assert without_threading.sections == []  # 216 > 0 + default gap of 5: rejected cold

    with_threading = split_by_sequential_numbered_headings(
        continuation_page, label_prefix="Section", start_last_number=first.last_accepted_number
    )
    assert [label for label, _ in with_threading.sections] == ["Section 216"]


def test_a_page_with_no_headings_returns_empty_and_preserves_the_counter():
    result = split_by_sequential_numbered_headings(
        "Just ordinary body text with no numbered heading at all.",
        label_prefix="Section",
        start_last_number=150,
    )
    assert result.sections == []
    assert result.last_accepted_number == 150


def test_a_document_with_no_matching_shape_at_all_yields_empty_sections():
    result = split_by_sequential_numbered_headings("No headings here whatsoever, just prose.")
    assert result.sections == []
    assert result.last_accepted_number == 0


def test_rule_label_prefix_is_configurable_for_customs_rules_style_documents():
    text = "114.   Restrictions which do not affect value.-  Among restrictions which apply."
    result = split_by_sequential_numbered_headings(text, label_prefix="Rule", start_last_number=110)
    assert result.sections == [("Rule 114", text.strip())]
