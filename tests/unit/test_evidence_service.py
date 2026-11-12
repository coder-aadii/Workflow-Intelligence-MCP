"""Unit tests for EvidenceService fuzzy matching and exact verification."""

from __future__ import annotations

import pytest

from workflow_intelligence.services.evidence_service import (
    EvidenceService,
    exact_email_domain_match,
    exact_tax_id_match,
    levenshtein_similarity,
    token_sort_similarity,
)


@pytest.fixture
def evidence() -> EvidenceService:
    """Provide an EvidenceService instance."""
    return EvidenceService()


# ---------------------------------------------------------------------------
# Levenshtein similarity tests
# ---------------------------------------------------------------------------


class TestLevenshteinSimilarity:
    """Tests for levenshtein_similarity."""

    def test_identical_strings_score_100(self, evidence: EvidenceService) -> None:
        """Identical strings should score 100."""
        assert levenshtein_similarity("ABC Logistics", "ABC Logistics") == 100.0

    def test_empty_strings_score_100(self, evidence: EvidenceService) -> None:
        """Two empty strings should score 100."""
        assert levenshtein_similarity("", "") == 100.0

    def test_one_empty_score_0(self, evidence: EvidenceService) -> None:
        """One empty string should score 0."""
        assert levenshtein_similarity("ABC", "") == 0.0
        assert levenshtein_similarity("", "ABC") == 0.0

    def test_single_char_difference(self, evidence: EvidenceService) -> None:
        """Single character difference should score high but not 100."""
        score = levenshtein_similarity("ABC Logistics", "ABC Logistcs")
        assert 0.0 < score < 100.0
        assert score > 90.0

    def test_case_insensitive(self, evidence: EvidenceService) -> None:
        """Similarity should be case-insensitive."""
        assert levenshtein_similarity("ABC", "abc") == 100.0


# ---------------------------------------------------------------------------
# Token sort similarity tests
# ---------------------------------------------------------------------------


class TestTokenSortSimilarity:
    """Tests for token_sort_similarity."""

    def test_word_reordering(self, evidence: EvidenceService) -> None:
        """Reordered words should score high."""
        score = token_sort_similarity("ABC Logistics Global", "Global ABC Logistics")
        assert score == 100.0

    def test_different_words_score_lower(self, evidence: EvidenceService) -> None:
        """Completely different words should score lower."""
        score = token_sort_similarity("ABC Logistics", "XYZ Shipping")
        assert score < 50.0

    def test_identical_strings_score_100(self, evidence: EvidenceService) -> None:
        """Identical strings should score 100."""
        assert token_sort_similarity("ABC Logistics", "ABC Logistics") == 100.0


# ---------------------------------------------------------------------------
# Exact match tests
# ---------------------------------------------------------------------------


class TestExactMatches:
    """Tests for exact tax ID and email domain matching."""

    def test_exact_tax_id_match(self) -> None:
        """Normalized tax IDs should match exactly."""
        assert exact_tax_id_match("US-123456789", "US123456789") is True
        assert exact_tax_id_match("US-123-456-789", "US123456789") is True

    def test_exact_tax_id_no_match(self) -> None:
        """Different tax IDs should not match."""
        assert exact_tax_id_match("US-123456789", "US-987654321") is False

    def test_exact_tax_id_empty(self) -> None:
        """Empty tax IDs should not match."""
        assert exact_tax_id_match("", "US-123456789") is False
        assert exact_tax_id_match("US-123456789", "") is False

    def test_exact_email_domain_match(self) -> None:
        """Matching email domains should return True."""
        assert exact_email_domain_match("user@abc.com", "admin@abc.com") is True
        assert exact_email_domain_match("User@ABC.COM", "admin@abc.com") is True

    def test_exact_email_domain_no_match(self) -> None:
        """Different email domains should return False."""
        assert exact_email_domain_match("user@abc.com", "admin@xyz.com") is False

    def test_exact_email_domain_empty(self) -> None:
        """Empty emails should not match."""
        assert exact_email_domain_match("", "admin@abc.com") is False
        assert exact_email_domain_match("user@abc.com", "") is False


# ---------------------------------------------------------------------------
# EvidenceService.match_entity_name tests
# ---------------------------------------------------------------------------


class TestMatchEntityName:
    """Tests for EvidenceService.match_entity_name."""

    def test_exact_name_match(self, evidence: EvidenceService) -> None:
        """Exact name match should score 100."""
        score = evidence.match_entity_name("ABC Logistics", "ABC Logistics")
        assert score == 100.0

    def test_typo_variation_high_score(self, evidence: EvidenceService) -> None:
        """Typo variation should score high (>= 90)."""
        score = evidence.match_entity_name("ABC Logistics", "ABC Logistcs")
        assert score >= 90.0

    def test_different_names_low_score(self, evidence: EvidenceService) -> None:
        """Completely different names should score low."""
        score = evidence.match_entity_name("ABC Logistics", "XYZ Shipping")
        assert score < 50.0


# ---------------------------------------------------------------------------
# EvidenceService.match_entities tests
# ---------------------------------------------------------------------------


class TestMatchEntities:
    """Tests for EvidenceService.match_entities."""

    def test_match_with_tax_id_exact(self, evidence: EvidenceService) -> None:
        """Exact tax ID match should yield high confidence even with name typo."""
        payload = {
            "vendor_name": "ABC Logistics Globla Inc",
            "tax_id": "US-123456789",
        }
        master_records = [
            {
                "entity_id": "VEND-001",
                "registered_name": "ABC Logistics Global Inc",
                "tax_id": "US-123456789",
            }
        ]
        matches = evidence.match_entities(payload, master_records)

        assert len(matches) == 1
        assert matches[0]["entity_id"] == "VEND-001"
        assert matches[0]["matching_score"] >= 95.0
        assert "vendor_name_typo" in (matches[0]["discrepancies"] or [])

    def test_match_no_candidates(self, evidence: EvidenceService) -> None:
        """No matching records should return empty list."""
        payload = {"vendor_name": "ZZZ Unknown Corp"}
        master_records = [
            {
                "entity_id": "VEND-001",
                "registered_name": "ABC Logistics Global Inc",
            }
        ]
        matches = evidence.match_entities(payload, master_records)
        assert matches == []

    def test_match_multiple_records_sorted(self, evidence: EvidenceService) -> None:
        """Multiple matches should be sorted by score descending."""
        payload = {"vendor_name": "ABC Logistics"}
        master_records = [
            {
                "entity_id": "VEND-001",
                "registered_name": "ABC Logistics Global Inc",
            },
            {
                "entity_id": "VEND-002",
                "registered_name": "ABC Logistics Inc",
            },
            {
                "entity_id": "VEND-003",
                "registered_name": "XYZ Shipping",
            },
        ]
        matches = evidence.match_entities(payload, master_records)

        assert len(matches) == 2  # XYZ Shipping should be excluded (< 50%)
        scores = [m["matching_score"] for m in matches]
        assert scores == sorted(scores, reverse=True)

    def test_match_entities_empty_master(self, evidence: EvidenceService) -> None:
        """Empty master records should return empty list."""
        matches = evidence.match_entities({"vendor_name": "ABC"}, [])
        assert matches == []

    def test_match_discrepancies_tax_id_mismatch(
        self, evidence: EvidenceService
    ) -> None:
        """Name match but tax ID mismatch should flag discrepancy."""
        payload = {
            "vendor_name": "ABC Logistics",
            "tax_id": "US-111",
        }
        master_records = [
            {
                "entity_id": "VEND-001",
                "registered_name": "ABC Logistics",
                "tax_id": "US-999",
            }
        ]
        matches = evidence.match_entities(payload, master_records)

        assert len(matches) == 1
        assert "tax_id_mismatch" in (matches[0]["discrepancies"] or [])
