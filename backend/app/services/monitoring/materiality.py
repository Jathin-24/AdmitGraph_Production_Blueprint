"""Materiality rules and human-readable explanations for monitor checks.

A *material* change is one that would matter to a student's decision:

* a deadline moving by at least one day,
* a cost (tuition/fee) moving by at least one currency unit or by 5%,
* an academic / prerequisite / scholarship value changing after case- and
  whitespace-insensitive normalization.

Equality-only differences — and differences that disappear after
normalization — are never material. This module only classifies the
difference between a previously stored value and a newly observed one; it
never invents or validates university facts (deadlines, tuition, rules).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

# The five field keys a subscription may watch (api/v1/monitor.py validates
# against this list).
FIELD_KEYS: tuple[str, ...] = ("deadline", "cost", "academic", "prerequisite", "scholarship")

FREQUENCY_DELTAS: dict[str, timedelta] = {
    "DAILY": timedelta(days=1),
    "WEEKLY": timedelta(days=7),
    "MONTHLY": timedelta(days=30),
}
DEFAULT_FREQUENCY = "WEEKLY"

FIELD_LABELS: dict[str, str] = {
    "deadline": "application deadline",
    "cost": "tuition and fees",
    "academic": "academic requirement",
    "prerequisite": "prerequisite",
    "scholarship": "scholarship",
}

# Any JSON scalar a snapshot stores (column is JSONB, annotated loosely in models).
ScalarValue = str | float | int | bool

# Dict keys consulted (in order) when collapsing an extracted value dict to a scalar.
_FIELD_VALUE_KEYS: dict[str, tuple[str, ...]] = {
    "deadline": ("application_deadline", "deadline", "last_date", "date", "value", "min", "max"),
    "cost": ("tuition_max", "tuition", "amount", "cost", "fee", "value", "max", "min"),
    "academic": ("academic_cgpa_min", "cgpa_min", "min", "value", "score", "max"),
    "prerequisite": ("prerequisite_subjects", "subjects", "value", "min"),
    "scholarship": ("name", "title", "amount", "value", "deadline", "description"),
}
_GENERIC_VALUE_KEYS: tuple[str, ...] = ("value", "amount", "date", "min", "max", "name", "title")

_DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d %Y",
    "%B %d %Y",
    "%Y/%m/%d",
    "%d.%m.%Y",
)
_WS_RE = re.compile(r"\s+")
_STRIP_NUMBER_RE = re.compile(r"[^0-9,.\-+]")
_FULL_NUMBER_RE = re.compile(r"^[+-]?[\d,.]+$")


def frequency_delta(frequency: str | None) -> timedelta:
    """How far the next check moves for DAILY / WEEKLY / MONTHLY (default WEEKLY)."""
    if frequency is None:
        return FREQUENCY_DELTAS[DEFAULT_FREQUENCY]
    return FREQUENCY_DELTAS.get(str(frequency).strip().upper(), FREQUENCY_DELTAS[DEFAULT_FREQUENCY])


def next_check_time(now: datetime, frequency: str | None) -> datetime:
    """Timestamp of the next scheduled check after *now*."""
    return now + frequency_delta(frequency)


def _collapse(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def to_scalar(value: Any, field_key: str = "") -> ScalarValue | None:
    """Normalize a stored/observed value to a JSON scalar (string or number).

    Dicts collapse onto the field's most meaningful key (e.g. ``{"date": ...}``
    for a deadline, ``{"tuition_max": ...}`` for a cost); anything else falls
    back to a sorted ``key=value`` rendering so nothing is silently dropped.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return _collapse(value)
    if isinstance(value, (list, tuple)):
        parts = [to_scalar(item, field_key) for item in value]
        return ", ".join(str(part) for part in parts if part is not None)
    if isinstance(value, dict):
        preferred = _FIELD_VALUE_KEYS.get(field_key, ()) + _GENERIC_VALUE_KEYS
        visited: set[str] = set()
        for key in preferred:
            if key in visited:
                continue
            visited.add(key)
            if key in value and value[key] is not None:
                picked = to_scalar(value[key], field_key)
                if picked is not None:
                    return picked
        if len(value) == 1:
            picked = to_scalar(next(iter(value.values())), field_key)
            if picked is not None:
                return picked
        return ", ".join(f"{key}={to_scalar(value[key], field_key)}" for key in sorted(value))
    return str(value)


def parse_number(value: Any) -> float | None:
    """Best-effort numeric reading of a value (``"€12,000"`` -> 12000.0)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, str):
        text = _STRIP_NUMBER_RE.sub("", value)
        if not text:
            return None
        if "," in text and "." in text:
            text = text.replace(",", "")
        elif "," in text:
            tail = text.rsplit(",", 1)[-1]
            text = text.replace(",", "") if len(tail) == 3 else text.replace(",", ".")
        try:
            return float(text)
        except ValueError:
            return None
    return None


def parse_date(value: Any) -> date | None:
    """Best-effort calendar date for deadline comparisons (None when unclear)."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None or isinstance(value, bool) or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _canonical(value: Any) -> str:
    """Equality form: case- and whitespace-insensitive, numerically forgiving."""
    if isinstance(value, dict):
        return "|".join(f"{key}={_canonical(value[key])}" for key in sorted(value))
    if isinstance(value, (list, tuple)):
        return ",".join(_canonical(item) for item in value)
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float, Decimal)):
        return f"{float(value):g}"
    text = _collapse(str(value))
    if _FULL_NUMBER_RE.match(text):
        number = parse_number(text)
        if number is not None:
            return f"{number:g}"
    return text.casefold()


def is_material_change(old: Any, new: Any) -> bool:
    """Field-agnostic materiality (kept for callers without a field key).

    A missing side (None) is never material; equal-after-normalization values
    are never material; any other difference is.
    """
    if old is None or new is None:
        return False
    return _canonical(old) != _canonical(new)


def material_change_for(field_key: str, old: Any, new: Any) -> bool:
    """Materiality for a monitored field, per the rules in the module docstring."""
    if old is None or new is None:
        return False
    old_scalar = to_scalar(old, field_key)
    new_scalar = to_scalar(new, field_key)
    if old_scalar is None or new_scalar is None:
        return False
    if field_key == "deadline":
        old_date = parse_date(old_scalar)
        new_date = parse_date(new_scalar)
        if old_date is not None and new_date is not None:
            return abs((new_date - old_date).days) >= 1
        return _canonical(old_scalar) != _canonical(new_scalar)
    if field_key == "cost":
        old_number = parse_number(old_scalar)
        new_number = parse_number(new_scalar)
        if old_number is not None and new_number is not None:
            delta = abs(new_number - old_number)
            if delta >= 1.0:
                return True
            if delta > 0 and old_number != 0 and delta / abs(old_number) >= 0.05:
                return True
            return False
        return _canonical(old_scalar) != _canonical(new_scalar)
    # academic, prerequisite, scholarship and any other key: text materiality.
    return _canonical(old_scalar) != _canonical(new_scalar)


def format_value(value: Any, field_key: str) -> str:
    """Display rendering used inside explanations (never invents a value)."""
    scalar = to_scalar(value, field_key)
    if scalar is None:
        return "unknown"
    if isinstance(scalar, bool):
        return str(scalar)
    if field_key == "deadline":
        parsed = parse_date(scalar)
        if parsed is not None:
            return f"{parsed.day} {parsed.strftime('%b %Y')}"
    if field_key == "cost" and isinstance(scalar, (int, float)):
        number = float(scalar)
        return f"{number:,.0f}" if number.is_integer() else f"{number:,.2f}"
    if isinstance(scalar, (int, float)):
        return f"{float(scalar):g}"
    return str(scalar)


def explain_change(
    field_key: str,
    old: Any,
    new: Any,
    *,
    material: bool,
    source: str = "cached",
) -> str:
    """One human sentence for a check result.

    ``source`` is the honesty marker: ``"cached"`` means the new value came
    from the latest *stored* evidence (no fresh search/observation), which is
    appended to the sentence. ``"live"`` means the value was just observed.
    """
    if not material:
        text = "No material change detected"
    elif field_key == "deadline":
        text = (
            f"Deadline moved from {format_value(old, 'deadline')} "
            f"to {format_value(new, 'deadline')}"
        )
    elif field_key == "cost":
        text = (
            f"Tuition changed from {format_value(old, 'cost')} "
            f"to {format_value(new, 'cost')}"
        )
        old_number = parse_number(to_scalar(old, field_key))
        new_number = parse_number(to_scalar(new, field_key))
        if old_number is not None and new_number is not None and old_number != 0:
            pct = (new_number - old_number) / abs(old_number) * 100.0
            text += f" ({pct:+.1f}%)"
    elif field_key == "academic":
        text = (
            f"Academic requirement changed from {format_value(old, field_key)} "
            f"to {format_value(new, field_key)}"
        )
    elif field_key == "prerequisite":
        text = (
            f"Prerequisite changed from {format_value(old, field_key)} "
            f"to {format_value(new, field_key)}"
        )
    elif field_key == "scholarship":
        text = (
            f"Scholarship information changed from {format_value(old, field_key)} "
            f"to {format_value(new, field_key)}"
        )
    else:
        text = (
            f"{FIELD_LABELS.get(field_key, field_key)} changed from "
            f"{format_value(old, field_key)} to {format_value(new, field_key)}"
        )
    if source == "cached":
        text += " (based on last stored evidence)"
    return text
