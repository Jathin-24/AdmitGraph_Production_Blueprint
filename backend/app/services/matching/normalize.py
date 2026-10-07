from datetime import date
from decimal import Decimal


def normalize_cgpa_to_percentage(cgpa: Decimal, scale: Decimal) -> Decimal | None:
    if scale <= 0:
        return None
    return (cgpa / scale) * Decimal(100)


def normalize_percentage_to_cgpa(percentage: Decimal, scale: Decimal) -> Decimal | None:
    if percentage < 0 or percentage > 100:
        return None
    return (percentage / Decimal(100)) * scale


def is_test_expired(expiry_date: date | None, today: date | None = None) -> bool:
    if expiry_date is None:
        return False
    return expiry_date < (today or date.today())
