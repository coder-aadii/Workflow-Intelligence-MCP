"""Evidence service for fuzzy entity reconciliation and exact-match verification."""

from __future__ import annotations

import re
from typing import Any


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Compute the Levenshtein edit distance between two strings.

    Args:
        s1: First string.
        s2: Second string.

    Returns:
        Edit distance (number of insertions, deletions, substitutions).
    """
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    previous_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1, start=1):
        current_row = [i]
        for j, c2 in enumerate(s2, start=1):
            insertions = previous_row[j] + 1
            deletions = current_row[j - 1] + 1
            substitutions = previous_row[j - 1] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


def levenshtein_similarity(s1: str, s2: str) -> float:
    """Compute Levenshtein-based similarity score (0-100).

    Args:
        s1: First string.
        s2: Second string.

    Returns:
        Similarity score where 100 means identical strings.
    """
    if not s1 and not s2:
        return 100.0
    if not s1 or not s2:
        return 0.0

    distance = _levenshtein_distance(s1.lower(), s2.lower())
    max_len = max(len(s1), len(s2))
    return round((1.0 - distance / max_len) * 100.0, 2)


def token_sort_similarity(s1: str, s2: str) -> float:
    """Compute token-sort similarity score (0-100).

    Tokenizes both strings, sorts tokens alphabetically, then compares
    the sorted token sequences using Levenshtein similarity. This handles
    word reordering (e.g. "ABC Logistics" vs "Logistics ABC").

    Args:
        s1: First string.
        s2: Second string.

    Returns:
        Similarity score where 100 means identical token sets.
    """
    if not s1 and not s2:
        return 100.0
    if not s1 or not s2:
        return 0.0

    tokens1 = sorted(s1.lower().split())
    tokens2 = sorted(s2.lower().split())
    sorted1 = " ".join(tokens1)
    sorted2 = " ".join(tokens2)

    return levenshtein_similarity(sorted1, sorted2)


def exact_tax_id_match(submitted: str, registered: str) -> bool:
    """Verify exact match on tax IDs / registration codes.

    Normalizes by stripping whitespace and non-alphanumeric characters
    and comparing case-insensitively.

    Args:
        submitted: Submitted tax ID / registration code.
        registered: Registered tax ID / registration code.

    Returns:
        True if the normalized values match exactly.
    """
    if not submitted or not registered:
        return False

    def _normalize(x: str) -> str:
        return re.sub(r"[^a-z0-9]", "", x.lower())

    return _normalize(submitted) == _normalize(registered)


def exact_email_domain_match(submitted: str, registered: str) -> bool:
    """Verify exact match on email domains.

    Extracts the domain portion after '@' and compares case-insensitively.

    Args:
        submitted: Submitted email address.
        registered: Registered email address.

    Returns:
        True if the domains match exactly.
    """
    if not submitted or not registered:
        return False

    def _domain(email: str) -> str:
        parts = email.strip().rsplit("@", 1)
        return parts[1].lower() if len(parts) == 2 else ""

    return _domain(submitted) == _domain(registered) and bool(_domain(submitted))


class EvidenceService:
    """Service for fuzzy entity reconciliation and exact-match verification.

    Provides:
    - Levenshtein similarity scoring for entity names
    - Token-sort similarity for word-reordered names
    - Exact-match verification on tax IDs, registration codes, email domains
    - Combined matching against master data records
    """

    def match_entity_name(self, submitted_name: str, registered_name: str) -> float:
        """Compute a combined name similarity score (0-100).

        Uses the maximum of Levenshtein and token-sort similarity to
        capture both typo variations and word reordering.

        Args:
            submitted_name: Submitted entity name.
            registered_name: Registered entity name.

        Returns:
            Combined similarity score (0-100).
        """
        lev_score = levenshtein_similarity(submitted_name, registered_name)
        token_score = token_sort_similarity(submitted_name, registered_name)
        return round(max(lev_score, token_score), 2)

    def match_entities(
        self,
        payload: dict[str, Any],
        master_records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Match submitted entities against master data records.

        For each master record, computes:
        - Name similarity (Levenshtein + token-sort)
        - Exact tax ID / registration code match
        - Exact email domain match

        Returns matches sorted by confidence score descending, including
        only records with a name similarity >= 50% or an exact ID match.

        Args:
            payload: Failure payload containing submitted entity data.
                Expected keys may include: vendor_name, tax_id,
                registration_code, email.
            master_records: List of master data records. Each record may
                contain: entity_id, registered_name, tax_id,
                registration_code, email.

        Returns:
            List of match dictionaries with keys: entity_id, registered_name,
            matching_score, discrepancies.
        """
        submitted_name = str(
            payload.get("vendor_name")
            or payload.get("entity_name")
            or payload.get("name")
            or ""
        )
        submitted_tax_id = str(
            payload.get("tax_id") or payload.get("registration_code") or ""
        )
        submitted_email = str(payload.get("email") or "")

        matches: list[dict[str, Any]] = []

        for record in master_records:
            registered_name = str(
                record.get("registered_name") or record.get("name") or ""
            )
            registered_tax_id = str(
                record.get("tax_id") or record.get("registration_code") or ""
            )
            registered_email = str(record.get("email") or "")

            name_score = self.match_entity_name(submitted_name, registered_name)
            tax_id_match = exact_tax_id_match(submitted_tax_id, registered_tax_id)
            email_match = exact_email_domain_match(submitted_email, registered_email)

            # High confidence if exact tax ID match OR name similarity >= 95
            confidence = name_score
            if tax_id_match:
                confidence = max(confidence, 95.0)
            if email_match:
                confidence = max(confidence, 90.0)

            # Include if high name similarity or exact ID/email match
            if name_score >= 50.0 or tax_id_match or email_match:
                discrepancies: list[str] = []
                if name_score < 95.0:
                    discrepancies.append("vendor_name_typo")
                if submitted_tax_id and registered_tax_id and not tax_id_match:
                    discrepancies.append("tax_id_mismatch")
                if submitted_email and registered_email and not email_match:
                    discrepancies.append("email_domain_mismatch")

                matches.append(
                    {
                        "entity_id": str(record.get("entity_id") or ""),
                        "registered_name": registered_name,
                        "matching_score": round(confidence, 2),
                        "discrepancies": discrepancies if discrepancies else None,
                    }
                )

        matches.sort(key=lambda m: m["matching_score"], reverse=True)
        return matches
