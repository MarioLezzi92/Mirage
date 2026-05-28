from rapidfuzz import fuzz


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return value.strip().lower()


def name_similarity(name_a: str | None, name_b: str | None) -> float:
    a = normalize_text(name_a)
    b = normalize_text(name_b)

    if not a or not b:
        return 0.0

    return fuzz.token_sort_ratio(a, b) / 100


def exact_or_partial_score(expected: str | None, found: str | None) -> float:
    expected_norm = normalize_text(expected)
    found_norm = normalize_text(found)

    if not expected_norm or not found_norm:
        return 0.0

    if expected_norm == found_norm:
        return 1.0

    if expected_norm in found_norm or found_norm in expected_norm:
        return 0.75

    return fuzz.partial_ratio(expected_norm, found_norm) / 100


def clamp_score(value: float) -> float:
    return max(0.0, min(value, 1.0))