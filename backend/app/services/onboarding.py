"""Backend-driven onboarding schema and answer mapping."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import (
    EducationRecord,
    EducationSubject,
    Skill,
    StudentProfile,
    TestScore,
)
from app.db.repositories import onboarding as onboarding_repo
from app.schemas.onboarding import OnboardingField, OnboardingSchema, OnboardingStep
from app.services.profile import TRACKED_FIELDS, get_or_create_profile

ONBOARDING_SCHEMA = OnboardingSchema(
    version="v1",
    steps=[
        OnboardingStep(
            id="goal",
            title="Goal",
            fields=[
                OnboardingField(
                    key="field_of_study",
                    question="What field do you want to study?",
                    explanation="This helps us find programs that match your interests.",
                    example="Computer Science, Artificial Intelligence",
                    why_we_ask="Programs are organized by field; guessing here wastes your time later.",
                    input_type="text",
                    required=True,
                ),
                OnboardingField(
                    key="career_goal",
                    question="What is your career goal?",
                    explanation="Where you want to end up shapes which programs are worth your money.",
                    example="ML engineer in Germany",
                    why_we_ask="Career-fit scoring compares program outcomes to this goal.",
                    input_type="text",
                ),
                OnboardingField(
                    key="preferred_countries",
                    question="Which countries interest you?",
                    explanation="You can change this later.",
                    example="Germany, Netherlands",
                    why_we_ask="Country choice drives visa, tuition, and deadline rules.",
                    input_type="list",
                ),
                OnboardingField(
                    key="target_intakes",
                    question="When do you want to start?",
                    explanation="An intake is the term a program admits new students.",
                    example="Winter 2027",
                    why_we_ask="Deadlines and offers depend on intake.",
                    input_type="list",
                ),
            ],
        ),
        OnboardingStep(
            id="education",
            title="Education",
            fields=[
                OnboardingField(
                    key="current_degree",
                    question="What is your current degree?",
                    explanation="Your background determines which programs you can enter.",
                    example="B.Tech Computer Science",
                    why_we_ask="Most master's programs require a related bachelor's degree.",
                    input_type="text",
                    required=True,
                ),
                OnboardingField(
                    key="institution_name",
                    question="Which institution?",
                    explanation="Programs may weigh your institution differently.",
                    example="IIT Bombay",
                    why_we_ask="Important for credential evaluation; mark UNKNOWN if unsure.",
                    input_type="text",
                ),
                OnboardingField(
                    key="cgpa",
                    question="Your CGPA?",
                    explanation="CGPA is your cumulative grade point average.",
                    example="8.1",
                    why_we_ask="Used to check academic minimums.",
                    input_type="number",
                    required=True,
                ),
                OnboardingField(
                    key="graduation_year",
                    question="Expected graduation year?",
                    explanation="Most intakes expect a recent or upcoming degree.",
                    example="2026",
                    why_we_ask="Determines which intake you can realistically target.",
                    input_type="number",
                ),
                OnboardingField(
                    key="cgpa_scale",
                    question="Your CGPA is out of what scale?",
                    explanation="The maximum CGPA your transcript uses — 10 in India, 4.0 in the US.",
                    example="10",
                    why_we_ask="An 8.1/10 and a 3.3/4.0 are only comparable once we put them on one scale.",
                    input_type="number",
                ),
                OnboardingField(
                    key="percentage",
                    question="Or your percentage, if your transcript shows one?",
                    explanation="Some transcripts report a percentage instead of (or alongside) a CGPA.",
                    example="78.5",
                    why_we_ask="Percentages run the same academic fit check when a CGPA is missing.",
                    input_type="number",
                ),
                OnboardingField(
                    key="backlogs",
                    question="How many backlogs do you have?",
                    explanation="A backlog is a course you failed and retook; enter 0 if you have none.",
                    example="0",
                    why_we_ask="Some programs cap how many backlogs they accept — better to know now.",
                    input_type="number",
                ),
            ],
        ),
        OnboardingStep(
            id="tests",
            title="Tests",
            fields=[
                OnboardingField(
                    key="english_test_overall",
                    question="English test overall score?",
                    explanation=(
                        "IELTS/TOEFL/PTE prove language ability; scores usually expire after 2 years."
                    ),
                    example="IELTS 7.5",
                    why_we_ask=(
                        "Language fit cannot be computed without it. If you have none, "
                        "we flag a risk instead of guessing."
                    ),
                    input_type="number",
                ),
            ],
        ),
        OnboardingStep(
            id="experience",
            title="Experience",
            fields=[
                OnboardingField(
                    key="total_experience_months",
                    question="How much work or internship experience do you have?",
                    explanation=(
                        "Count full-time jobs and internships in months; substantial "
                        "projects count too."
                    ),
                    example="24",
                    why_we_ask=(
                        "Some master's programs expect professional experience, and it is "
                        "the evidence behind your career-fit score."
                    ),
                    input_type="number",
                ),
                OnboardingField(
                    key="skills",
                    question="Which skills should we highlight?",
                    explanation="Tools, languages, methods — whatever you would list on a CV.",
                    example="Python, SQL, Docker",
                    why_we_ask="Skills connect your background to specializations that actually fit it.",
                    input_type="list",
                ),
            ],
        ),
        OnboardingStep(
            id="budget",
            title="Budget",
            fields=[
                OnboardingField(
                    key="total_budget_amount",
                    question="Total budget for your studies?",
                    explanation="Includes tuition and living costs.",
                    example="1800000 INR",
                    why_we_ask="Financial fit prevents plans you cannot fund.",
                    input_type="number",
                    required=True,
                ),
                OnboardingField(
                    key="annual_budget_amount",
                    question="What can you spend per year?",
                    explanation="One academic year of tuition plus living costs, if you know it.",
                    example="300000 INR",
                    why_we_ask="A plan can fit your total budget and still break on a single expensive year.",
                    input_type="number",
                ),
                OnboardingField(
                    key="scholarship_dependence",
                    question="Will you depend on a scholarship?",
                    explanation="Pick Yes if you cannot fund the degree without one.",
                    example="No",
                    why_we_ask=(
                        "Scholarship dependence turns a missing scholarship from a disappointment "
                        "into a funding risk we track for you."
                    ),
                    input_type="choice",
                    options=["Yes", "No"],
                ),
            ],
        ),
        OnboardingStep(
            id="preferences",
            title="Preferences",
            fields=[
                OnboardingField(
                    key="preferred_degree_types",
                    question="Which degree types are you considering?",
                    explanation=(
                        "A master's is the standard postgraduate degree; it may be called "
                        "MSc, MA, MEng or M.Tech depending on the country."
                    ),
                    example="Master's",
                    why_we_ask="Degree type narrows the program list before anything else does.",
                    input_type="list",
                ),
                OnboardingField(
                    key="preferred_cities",
                    question="Any cities you would like to live in?",
                    explanation="Optional — a short list, or leave it blank.",
                    example="Munich, Berlin",
                    why_we_ask="City choice moves living costs and your access to graduate jobs.",
                    input_type="list",
                ),
                OnboardingField(
                    key="excluded_countries",
                    question="Any countries you would rather rule out?",
                    explanation="Excluded countries stay out of your plan — you can revisit this later.",
                    example="France",
                    why_we_ask="Hard exclusions keep dead-end recommendations out of your portfolio.",
                    input_type="list",
                ),
                OnboardingField(
                    key="career_market_importance",
                    question="How important is the job market after graduation (0–10)?",
                    explanation="0 = the degree itself is the point; 10 = employment decides everything.",
                    example="8",
                    why_we_ask="It balances career outcomes against tuition and admission risk.",
                    input_type="number",
                ),
                OnboardingField(
                    key="research_preference",
                    question="Research-heavy or coursework?",
                    explanation=(
                        "Some programs are thesis-based and end in a dissertation; others are "
                        "taught like a structured course."
                    ),
                    example="Coursework",
                    why_we_ask=(
                        "It separates programs that train researchers from those that "
                        "train practitioners."
                    ),
                    input_type="choice",
                    options=["Research-heavy", "Mixed", "Coursework"],
                ),
            ],
        ),
        OnboardingStep(
            id="review",
            title="Review",
            fields=[],
        ),
    ],
)

PROFILE_FIELD_MAP = {
    "field_of_study",
    "career_goal",
    "current_degree",
    "institution_name",
    "cgpa",
    "graduation_year",
    "total_budget_amount",
    # Education extras (existing StudentProfile columns).
    "cgpa_scale",
    "percentage",
    "backlogs",
    # Experience step (existing StudentProfile column).
    "total_experience_months",
    # Budget step (existing StudentProfile column).
    "annual_budget_amount",
    "scholarship_dependence",
}
PREFERENCE_FIELD_MAP = {"preferred_countries", "target_intakes"}
# New preference keys, all mapped onto existing ProfilePreference columns.
PREFERENCE_LIST_MAP = {"preferred_degree_types", "preferred_cities", "excluded_countries"}
PREFERENCE_TEXT_MAP = {"research_preference"}
PREFERENCE_DECIMAL_MAP = {"career_market_importance"}
# Answer keys with no single profile column: they live in their own table.
SKILL_FIELD_MAP = {"skills"}

# Form inputs arrive as strings; these keys target typed columns and must be
# converted before touching the database (a string in SMALLINT is a 500).
INT_FIELDS = {"graduation_year", "backlogs", "total_experience_months"}
DECIMAL_FIELDS = {
    "cgpa",
    "total_budget_amount",
    "cgpa_scale",
    "percentage",
    "annual_budget_amount",
}
BOOL_FIELDS = {"scholarship_dependence"}
TEST_SCORE_FIELDS = {"english_test_overall"}
ENGLISH_TEST_TYPE = "english_overall"
# Payload-only companions to the score answer (all inside `answers`): which
# English test was actually taken, when it was taken, and when it expires.
# Legacy payloads omit all three and keep writing `english_overall` rows.
ENGLISH_TEST_META_FIELDS = ("english_test_type", "english_test_date", "english_expiry_date")
# English tests onboarding can record. Progress credits the score question from
# any of these (case-insensitive); `english_overall` is the legacy default and
# the canonical casing is what gets stored for known types.
ENGLISH_TEST_TYPES = ("english_overall", "IELTS", "IELTS_ACADEMIC", "TOEFL", "PTE", "DET")
ENGLISH_TEST_TYPE_BY_KEY = {t.lower(): t for t in ENGLISH_TEST_TYPES}
ENGLISH_TEST_TYPES_LOWER = frozenset(ENGLISH_TEST_TYPE_BY_KEY)
# Payload-only key: subjects have no StudentProfile column — they live on the
# profile's EducationRecord (existing tables, no migration).
SUBJECT_FIELDS = {"subjects"}
SKILL_SOURCE = "onboarding"

REQUIRED_KEYS = {f.key for s in ONBOARDING_SCHEMA.steps for f in s.fields if f.required}

# Progress cannot read a user's mind: columns whose PostgreSQL default makes a
# fresh profile look answered (server_default 0 / false) are only credited when
# the stored value differs from that default. A genuine "0 months"/"No" answer
# earns no progress points instead of inflating everyone's completion.
DEFAULTED_VALUE_BY_KEY: dict[str, Any] = {
    "backlogs": 0,
    "total_experience_months": 0,
    "scholarship_dependence": False,
}
PROFILE_PROGRESS_KEYS = [
    "percentage",
    "cgpa_scale",
    "backlogs",
    "total_experience_months",
    "scholarship_dependence",
]
PREFERENCE_PROGRESS_LIST_KEYS = [
    "preferred_countries",
    "target_intakes",
    "preferred_degree_types",
    "preferred_cities",
    "excluded_countries",
]
PREFERENCE_PROGRESS_TEXT_KEYS = ["research_preference"]
PREFERENCE_PROGRESS_DECIMAL_KEYS = ["career_market_importance"]


def _to_decimal(key: str, raw: Any) -> Decimal | None:
    """Coerce a form value to Decimal; blank/None clears the value."""
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return None
    try:
        value = Decimal(str(raw))
    except InvalidOperation:
        raise AppError(400, "VALIDATION_ERROR", f"Invalid number for {key}") from None
    if not value.is_finite():
        raise AppError(400, "VALIDATION_ERROR", f"Invalid number for {key}") from None
    return value


def _to_int(key: str, raw: Any) -> int | None:
    value = _to_decimal(key, raw)
    if value is None:
        return None
    if value != value.to_integral_value():
        raise AppError(400, "VALIDATION_ERROR", f"Expected a whole number for {key}")
    return int(value)


def _to_list(raw: Any) -> list[str]:
    """Normalize a list answer: arrays pass through, comma text is split."""
    if raw is None:
        return []
    if isinstance(raw, list):
        items = [str(v).strip() for v in raw]
    else:
        items = [part.strip() for part in str(raw).split(",")]
    return [item for item in items if item]


_TRUE_WORDS = {"true", "yes", "y", "1", "on"}
_FALSE_WORDS = {"false", "no", "n", "0", "off"}


def _to_bool(key: str, raw: Any) -> bool | None:
    """Coerce a yes/no form value to bool; blank/None clears the value."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if not text:
        return None
    if text in _TRUE_WORDS:
        return True
    if text in _FALSE_WORDS:
        return False
    raise AppError(400, "VALIDATION_ERROR", f"Expected yes or no for {key}")


def _to_date(key: str, raw: Any) -> date | None:
    """Coerce a YYYY-MM-DD form value to date; blank/None clears the column."""
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except (TypeError, ValueError):
        raise AppError(400, "VALIDATION_ERROR", f"Expected a date (YYYY-MM-DD) for {key}") from None


def _english_test_type(answers: dict[str, Any]) -> str:
    """The REAL test type from the payload; legacy payloads keep the default."""
    raw = answers.get("english_test_type")
    if raw is None:
        return ENGLISH_TEST_TYPE
    text = str(raw).strip()
    if not text:
        return ENGLISH_TEST_TYPE
    # Known tests are stored with canonical casing (IELTS, not "iElTs") so the
    # exact-match readers (strategies.py, persist.py) find them; anything else
    # is stored exactly as the user reported it — never guessed.
    return ENGLISH_TEST_TYPE_BY_KEY.get(text.lower(), text)


async def _upsert_english_score(
    session: AsyncSession, profile_id: UUID, answers: dict[str, Any]
) -> None:
    """Upsert the profile's English TestScore from score + companion answers.

    The row is keyed by the real test type (`english_test_type`, defaulting to
    the legacy `english_overall`); an explicit type first adopts the legacy row
    so a legacy-to-new upgrade never duplicates the score. A blank score still
    deletes (legacy clear semantics — date companions are irrelevant then);
    date companions apply only when their key is present, so a plain score
    update never wipes dates set earlier.
    """
    test_type = _english_test_type(answers)
    row = await onboarding_repo.english_test_score(session, profile_id, test_type)
    if row is None and test_type != ENGLISH_TEST_TYPE:
        legacy = await onboarding_repo.english_test_score(session, profile_id, ENGLISH_TEST_TYPE)
        if legacy is not None:
            legacy.test_type = test_type  # adopt the row onboarding already owns
            row = legacy

    if "english_test_overall" in answers:
        score = _to_decimal("english_test_overall", answers["english_test_overall"])
        if score is None:
            if row is not None:
                await session.delete(row)
            return
        if row is None:
            row = TestScore(profile_id=profile_id, test_type=test_type, overall_score=score)
            session.add(row)
        else:
            row.overall_score = score
    elif row is None:
        # Type/dates without a score record what was reported honestly:
        # overall_score stays NULL (progress only credits scored rows).
        row = TestScore(profile_id=profile_id, test_type=test_type)
        session.add(row)
    if "english_test_date" in answers:
        row.test_date = _to_date("english_test_date", answers["english_test_date"])
    if "english_expiry_date" in answers:
        row.expiry_date = _to_date("english_expiry_date", answers["english_expiry_date"])


def _to_subjects(raw: Any) -> list[tuple[str, Decimal | None]]:
    """Parse a subjects answer: legacy ["Math"] (or comma text) and
    [{"name": "Math", "credits": "4"}] objects both work.

    Blank names are dropped, duplicates (case-insensitive) collapse, and
    malformed objects are a 400 instead of silently storing garbage.
    """
    if raw is None:
        items: list[Any] = []
    elif isinstance(raw, dict):
        items = [raw]
    elif isinstance(raw, list):
        items = raw
    else:
        items = _to_list(raw)
    subjects: list[tuple[str, Decimal | None]] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        credits: Decimal | None = None
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if not name:
                raise AppError(400, "VALIDATION_ERROR", f"Subject #{index + 1} needs a name")
            raw_credits = item.get("credits")
            if raw_credits not in (None, ""):
                credits = _to_decimal(f"subjects[{index}].credits", raw_credits)
                if credits is not None and credits < 0:
                    raise AppError(
                        400, "VALIDATION_ERROR", f"Subject credits cannot be negative ({name})"
                    )
        else:
            name = str(item).strip()
        if not name:
            continue
        dedupe_key = name.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        subjects.append((name, credits))
    return subjects


async def _education_record(
    session: AsyncSession, profile: StudentProfile
) -> EducationRecord | None:
    """The profile's current (else newest) education record, if any."""
    return await onboarding_repo.current_education_record(session, profile.id)


async def _replace_subjects(
    session: AsyncSession, profile: StudentProfile, raw: Any
) -> None:
    """Replace onboarding-captured subjects on the profile's education record.

    The newest/current EducationRecord is reused, or created from the profile's
    education answers when the profile has none yet (degree/institution are NOT
    NULL columns; they stay blank until the wizard's education answers arrive —
    re-submission then refreshes them). Payloads without `subjects` never reach
    this function, so legacy flows keep their UNKNOWN prerequisite state.
    """
    subjects = _to_subjects(raw)
    record = await _education_record(session, profile)
    if record is not None:
        for row in await onboarding_repo.education_subjects(session, record.id):
            await session.delete(row)
        # DELETEs must reach the database before the INSERTs (re-submission safety).
        await session.flush()
    if not subjects:
        return
    if record is None:
        record = EducationRecord(
            profile_id=profile.id,
            institution_name=profile.institution_name or "",
            degree=profile.current_degree or "",
            field_of_study=profile.field_of_study,
            is_current=True,
        )
        session.add(record)
        await session.flush()
    elif profile.current_degree:
        record.degree = profile.current_degree
        record.institution_name = profile.institution_name or record.institution_name
        record.field_of_study = profile.field_of_study or record.field_of_study
    for name, credits in subjects:
        session.add(
            EducationSubject(
                education_record_id=record.id,
                subject_name=name,
                credits=credits,
                normalized_subject=name.lower(),
            )
        )


async def _replace_skills(session: AsyncSession, profile_id: UUID, items: list[str]) -> None:
    """Replace the skills captured by onboarding (leaves other Skill rows alone)."""
    for row in await onboarding_repo.skills_by_source(session, profile_id, SKILL_SOURCE):
        await session.delete(row)
    if not items:
        return
    # DELETEs must reach the database before the INSERTs: skills carry a
    # UNIQUE (profile_id, skill_name) constraint.
    await session.flush()
    taken = set(await onboarding_repo.profile_skill_names(session, profile_id))
    for name in dict.fromkeys(items):
        if name in taken:
            continue  # already on the profile from another source
        session.add(Skill(profile_id=profile_id, skill_name=name, source=SKILL_SOURCE))


def answered_keys(profile_filled: set[str], preferences_filled: set[str]) -> list[str]:
    return sorted(profile_filled | preferences_filled)


def _is_answered(key: str, value: Any) -> bool:
    """True when the stored value reflects an answer (never a server default)."""
    if key in DEFAULTED_VALUE_BY_KEY:
        return value is not None and value != DEFAULTED_VALUE_BY_KEY[key]
    return value not in (None, "")


async def answered_from_db(session: AsyncSession) -> set[str]:
    """Every onboarding key the current profile genuinely answers, from the DB.

    Derived state only — same rules the wizard writes, so GET /onboarding/progress
    and POST /onboarding/answers can never disagree about what is answered.
    """
    profile = await get_or_create_profile(session)
    filled = {key for key in TRACKED_FIELDS if getattr(profile, key) not in (None, "")}
    for key in PROFILE_PROGRESS_KEYS:
        if _is_answered(key, getattr(profile, key, None)):
            filled.add(key)
    prefs = await onboarding_repo.profile_preference(session, profile.id)
    if prefs is not None:
        for key in PREFERENCE_PROGRESS_LIST_KEYS:
            if getattr(prefs, key, None):
                filled.add(key)
        for key in PREFERENCE_PROGRESS_TEXT_KEYS:
            if _is_answered(key, getattr(prefs, key, None)):
                filled.add(key)
        for key in PREFERENCE_PROGRESS_DECIMAL_KEYS:
            if getattr(prefs, key, None) is not None:
                filled.add(key)
    if await onboarding_repo.has_profile_skill(session, profile.id):
        filled.add("skills")
    if await onboarding_repo.has_scored_english_test(
        session, profile.id, ENGLISH_TEST_TYPES_LOWER
    ):
        filled.add("english_test_overall")
    return filled


async def apply_answers(session: AsyncSession, answers: dict[str, Any]) -> set[str]:
    """Persist onboarding answers with column-accurate types.

    Form values are strings: numeric fields are converted (or rejected with a
    400 instead of a 500), yes/no answers become booleans, blank values clear
    the column, list answers are split on commas, the English test row upserts
    from its whole payload (score + `english_test_type`/`english_test_date`/
    `english_expiry_date` companions), subjects replace the rows onboarding
    owns, and skills replace the rows onboarding owns.
    """
    profile = await get_or_create_profile(session)
    filled: set[str] = set()
    preferences = await onboarding_repo.profile_preference_required(session, profile.id)
    for key, value in answers.items():
        if key in PROFILE_FIELD_MAP:
            if key in BOOL_FIELDS:
                setattr(profile, key, _to_bool(key, value))
            elif key in INT_FIELDS:
                setattr(profile, key, _to_int(key, value))
            elif key in DECIMAL_FIELDS:
                setattr(profile, key, _to_decimal(key, value))
            else:
                text = value.strip() if isinstance(value, str) else value
                setattr(profile, key, text or None)
            filled.add(key)
        elif key in PREFERENCE_FIELD_MAP or key in PREFERENCE_LIST_MAP:
            setattr(preferences, key, _to_list(value))
            filled.add(key)
        elif key in PREFERENCE_TEXT_MAP:
            if value is None:
                text = ""
            else:
                text = value.strip() if isinstance(value, str) else str(value)
            setattr(preferences, key, text or None)
            filled.add(key)
        elif key in PREFERENCE_DECIMAL_MAP:
            setattr(preferences, key, _to_decimal(key, value))
            filled.add(key)
        elif key in SKILL_FIELD_MAP:
            await _replace_skills(session, profile.id, _to_list(value))
            filled.add(key)
    # The English test row is written from the whole payload (score, which test,
    # and its dates travel together), and subjects need the education answers
    # handled above — both therefore run after the column loop, so payload key
    # order can never matter.
    english_keys = TEST_SCORE_FIELDS | set(ENGLISH_TEST_META_FIELDS)
    handled_english = english_keys & answers.keys()
    if handled_english:
        await _upsert_english_score(session, profile.id, answers)
        filled.update(handled_english)
    if SUBJECT_FIELDS & answers.keys():
        await _replace_subjects(session, profile, answers["subjects"])
        filled.update(SUBJECT_FIELDS & answers.keys())
    await session.commit()
    return filled
