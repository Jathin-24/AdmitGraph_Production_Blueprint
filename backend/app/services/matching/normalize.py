from datetime import date
from decimal import Decimal


def normalize_cgpa_to_percentage(cgpa: Decimal, scale: Decimal) -> Decimal | None:
    """Map a CGPA onto the 0-100 percentage axis; None for a non-positive scale."""
    if scale <= 0:
        return None
    return (cgpa / scale) * Decimal(100)


def normalize_percentage_to_cgpa(percentage: Decimal, scale: Decimal) -> Decimal | None:
    """Map a 0-100 percentage onto a CGPA scale; None when conversion is undefined.

    Exact inverse of `normalize_cgpa_to_percentage`: for any scale > 0,
    round-tripping cgpa -> percentage -> cgpa returns the original value.
    A percentage outside [0, 100] or a non-positive scale (mirroring
    `normalize_cgpa_to_percentage`) has no defined conversion -> None, so
    callers fall back to UNKNOWN instead of comparing across scales.
    """
    if scale <= 0:
        return None
    if percentage < 0 or percentage > 100:
        return None
    return (percentage / Decimal(100)) * scale


def is_test_expired(expiry_date: date | None, today: date | None = None) -> bool:
    if expiry_date is None:
        return False
    return expiry_date < (today or date.today())
