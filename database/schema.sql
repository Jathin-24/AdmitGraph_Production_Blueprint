-- AdmitGraph PostgreSQL schema
-- Production-oriented normalized schema.
-- PostgreSQL 15+
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TYPE user_role AS ENUM ('STUDENT','ADMIN');
CREATE TYPE requirement_status AS ENUM ('SATISFIED','PARTIAL','NOT_SATISFIED','UNKNOWN','NOT_APPLICABLE','CONFLICTING','NEEDS_VERIFICATION');
CREATE TYPE risk_severity AS ENUM ('CRITICAL','HIGH','MEDIUM','LOW');
CREATE TYPE risk_status AS ENUM ('OPEN','ACKNOWLEDGED','RESOLVED','DISMISSED');
CREATE TYPE source_authority AS ENUM ('OFFICIAL_UNIVERSITY','OFFICIAL_GOVERNMENT','OFFICIAL_ORGANIZATION','ACCREDITED_BODY','CREDIBLE_SECONDARY','NEWS','FORUM_SOCIAL','UNKNOWN');
CREATE TYPE evidence_status AS ENUM ('CURRENT','STALE','CONFLICTING','UNAVAILABLE');
CREATE TYPE confidence_level AS ENUM ('HIGH','MEDIUM','LOW');
CREATE TYPE program_category AS ENUM ('REACH','TARGET','LOWER_RISK');
CREATE TYPE task_status AS ENUM ('TODO','IN_PROGRESS','DONE','BLOCKED','SKIPPED');
CREATE TYPE run_status AS ENUM ('QUEUED','RUNNING','SUCCEEDED','PARTIAL','FAILED','CANCELLED');

CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email TEXT NOT NULL UNIQUE,
  full_name TEXT,
  role user_role NOT NULL DEFAULT 'STUDENT',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE student_profiles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
  current_degree TEXT,
  field_of_study TEXT,
  institution_name TEXT,
  institution_country_code CHAR(2),
  graduation_year SMALLINT,
  cgpa NUMERIC(5,2),
  cgpa_scale NUMERIC(5,2),
  percentage NUMERIC(5,2),
  backlogs INTEGER DEFAULT 0 CHECK (backlogs >= 0),
  total_experience_months INTEGER DEFAULT 0 CHECK (total_experience_months >= 0),
  budget_currency CHAR(3) DEFAULT 'INR',
  total_budget_amount NUMERIC(14,2),
  annual_budget_amount NUMERIC(14,2),
  tuition_budget_amount NUMERIC(14,2),
  scholarship_dependence BOOLEAN DEFAULT FALSE,
  career_goal TEXT,
  profile_completion NUMERIC(5,2) DEFAULT 0,
  onboarding_version TEXT NOT NULL DEFAULT 'v1',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE profile_preferences (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  profile_id UUID NOT NULL REFERENCES student_profiles(id) ON DELETE CASCADE,
  preferred_countries JSONB NOT NULL DEFAULT '[]',
  excluded_countries JSONB NOT NULL DEFAULT '[]',
  preferred_cities JSONB NOT NULL DEFAULT '[]',
  preferred_degree_types JSONB NOT NULL DEFAULT '[]',
  preferred_fields JSONB NOT NULL DEFAULT '[]',
  target_intakes JSONB NOT NULL DEFAULT '[]',
  preferred_language TEXT,
  research_preference TEXT,
  career_market_importance NUMERIC(5,2),
  max_distance_preference TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE education_records (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  profile_id UUID NOT NULL REFERENCES student_profiles(id) ON DELETE CASCADE,
  institution_name TEXT NOT NULL,
  country_code CHAR(2),
  degree TEXT NOT NULL,
  field_of_study TEXT,
  start_date DATE,
  end_date DATE,
  grade_value NUMERIC(8,3),
  grade_scale NUMERIC(8,3),
  grade_type TEXT,
  is_current BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE education_subjects (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  education_record_id UUID NOT NULL REFERENCES education_records(id) ON DELETE CASCADE,
  subject_name TEXT NOT NULL,
  credits NUMERIC(8,2),
  grade_value TEXT,
  normalized_subject TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE test_scores (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  profile_id UUID NOT NULL REFERENCES student_profiles(id) ON DELETE CASCADE,
  test_type TEXT NOT NULL,
  overall_score NUMERIC(8,3),
  section_scores JSONB NOT NULL DEFAULT '{}',
  test_date DATE,
  expiry_date DATE,
  status TEXT DEFAULT 'VALID',
  evidence_id UUID,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE experiences (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  profile_id UUID NOT NULL REFERENCES student_profiles(id) ON DELETE CASCADE,
  experience_type TEXT NOT NULL,
  title TEXT NOT NULL,
  organization TEXT,
  description TEXT,
  start_date DATE,
  end_date DATE,
  metadata JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE skills (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  profile_id UUID NOT NULL REFERENCES student_profiles(id) ON DELETE CASCADE,
  skill_name TEXT NOT NULL,
  proficiency TEXT,
  months_experience INTEGER,
  source TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(profile_id, skill_name)
);

CREATE TABLE countries (
  code CHAR(2) PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  region TEXT,
  currency CHAR(3),
  metadata JSONB NOT NULL DEFAULT '{}'
);

CREATE TABLE institutions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  canonical_name TEXT NOT NULL,
  normalized_name TEXT NOT NULL,
  country_code CHAR(2) REFERENCES countries(code),
  city TEXT,
  website_url TEXT,
  domain TEXT,
  institution_type TEXT,
  authority_score NUMERIC(5,2) DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(normalized_name, country_code)
);

CREATE TABLE programs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  institution_id UUID NOT NULL REFERENCES institutions(id),
  canonical_name TEXT NOT NULL,
  normalized_name TEXT NOT NULL,
  degree_type TEXT,
  field_of_study TEXT,
  specialization TEXT,
  city TEXT,
  country_code CHAR(2) REFERENCES countries(code),
  language TEXT,
  official_url TEXT,
  duration_months INTEGER,
  tuition_amount NUMERIC(14,2),
  tuition_currency CHAR(3),
  metadata JSONB NOT NULL DEFAULT '{}',
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_verified_at TIMESTAMPTZ,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  UNIQUE(institution_id, normalized_name)
);

CREATE TABLE intakes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  program_id UUID NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
  intake_label TEXT NOT NULL,
  intake_year SMALLINT NOT NULL,
  start_date DATE,
  application_deadline DATE,
  deadline_type TEXT,
  status TEXT DEFAULT 'OPEN',
  evidence_id UUID,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(program_id, intake_label, intake_year)
);

CREATE TABLE requirements (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  program_id UUID NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
  requirement_type TEXT NOT NULL,
  title TEXT NOT NULL,
  normalized_key TEXT NOT NULL,
  operator TEXT,
  value JSONB NOT NULL,
  mandatory BOOLEAN NOT NULL DEFAULT TRUE,
  applies_to JSONB NOT NULL DEFAULT '{}',
  status requirement_status NOT NULL DEFAULT 'UNKNOWN',
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_verified_at TIMESTAMPTZ,
  UNIQUE(program_id, normalized_key)
);

CREATE TABLE requirement_versions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  requirement_id UUID NOT NULL REFERENCES requirements(id) ON DELETE CASCADE,
  value JSONB NOT NULL,
  status requirement_status NOT NULL,
  valid_from TIMESTAMPTZ NOT NULL DEFAULT now(),
  valid_to TIMESTAMPTZ,
  change_reason TEXT,
  evidence_id UUID
);

CREATE TABLE sources (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  url TEXT NOT NULL,
  canonical_url TEXT NOT NULL,
  domain TEXT NOT NULL,
  title TEXT,
  source_authority source_authority NOT NULL DEFAULT 'UNKNOWN',
  publisher TEXT,
  country_code CHAR(2),
  source_type TEXT,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ,
  UNIQUE(canonical_url)
);

CREATE TABLE search_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id) ON DELETE SET NULL,
  engine TEXT NOT NULL,
  query TEXT NOT NULL,
  parameters JSONB NOT NULL DEFAULT '{}',
  serpapi_search_id TEXT,
  status run_status NOT NULL DEFAULT 'RUNNING',
  requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ,
  duration_ms INTEGER,
  result_count INTEGER,
  cache_hit BOOLEAN DEFAULT FALSE,
  error_code TEXT,
  error_message TEXT
);

CREATE TABLE search_results (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  search_run_id UUID NOT NULL REFERENCES search_runs(id) ON DELETE CASCADE,
  source_id UUID REFERENCES sources(id),
  position INTEGER,
  result_type TEXT,
  title TEXT,
  snippet TEXT,
  displayed_url TEXT,
  result_url TEXT,
  raw_payload JSONB NOT NULL DEFAULT '{}',
  content_hash TEXT,
  retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE evidence (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id UUID NOT NULL REFERENCES sources(id),
  search_result_id UUID REFERENCES search_results(id),
  claim_type TEXT NOT NULL,
  subject_type TEXT NOT NULL,
  subject_id UUID,
  claim TEXT NOT NULL,
  normalized_claim TEXT,
  snippet TEXT,
  summary TEXT,
  extracted_value JSONB NOT NULL DEFAULT '{}',
  authority_score NUMERIC(5,2) DEFAULT 0,
  confidence confidence_level NOT NULL DEFAULT 'LOW',
  status evidence_status NOT NULL DEFAULT 'CURRENT',
  retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  published_at TIMESTAMPTZ,
  freshness_deadline TIMESTAMPTZ,
  content_hash TEXT,
  conflict_group_id UUID,
  extraction_model TEXT,
  extraction_version TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE evidence_conflicts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conflict_key TEXT NOT NULL,
  description TEXT NOT NULL,
  resolution_status TEXT NOT NULL DEFAULT 'UNRESOLVED',
  preferred_evidence_id UUID REFERENCES evidence(id),
  resolution_reason TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at TIMESTAMPTZ
);

CREATE TABLE evidence_conflict_members (
  conflict_id UUID NOT NULL REFERENCES evidence_conflicts(id) ON DELETE CASCADE,
  evidence_id UUID NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
  PRIMARY KEY(conflict_id, evidence_id)
);

CREATE TABLE research_plans (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  profile_id UUID NOT NULL REFERENCES student_profiles(id) ON DELETE CASCADE,
  status run_status NOT NULL DEFAULT 'QUEUED',
  requested_goal JSONB NOT NULL,
  planned_queries JSONB NOT NULL DEFAULT '[]',
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE research_plan_steps (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  research_plan_id UUID NOT NULL REFERENCES research_plans(id) ON DELETE CASCADE,
  step_key TEXT NOT NULL,
  service_name TEXT NOT NULL,
  status run_status NOT NULL DEFAULT 'QUEUED',
  input JSONB NOT NULL DEFAULT '{}',
  output JSONB NOT NULL DEFAULT '{}',
  search_run_ids JSONB NOT NULL DEFAULT '[]',
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  error_message TEXT,
  UNIQUE(research_plan_id, step_key)
);

CREATE TABLE fit_assessments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  research_plan_id UUID REFERENCES research_plans(id) ON DELETE CASCADE,
  profile_id UUID NOT NULL REFERENCES student_profiles(id) ON DELETE CASCADE,
  program_id UUID NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
  academic_score NUMERIC(5,2),
  prerequisite_score NUMERIC(5,2),
  language_score NUMERIC(5,2),
  financial_score NUMERIC(5,2),
  career_score NUMERIC(5,2),
  timing_score NUMERIC(5,2),
  evidence_confidence_score NUMERIC(5,2),
  overall_score NUMERIC(5,2) NOT NULL,
  scoring_version TEXT NOT NULL,
  explanation TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE fit_dimension_evidence (
  fit_assessment_id UUID NOT NULL REFERENCES fit_assessments(id) ON DELETE CASCADE,
  dimension TEXT NOT NULL,
  evidence_id UUID NOT NULL REFERENCES evidence(id),
  PRIMARY KEY(fit_assessment_id, dimension, evidence_id)
);

CREATE TABLE eligibility_assessments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  fit_assessment_id UUID NOT NULL REFERENCES fit_assessments(id) ON DELETE CASCADE,
  requirement_id UUID NOT NULL REFERENCES requirements(id),
  status requirement_status NOT NULL,
  matched_value JSONB NOT NULL DEFAULT '{}',
  expected_value JSONB NOT NULL DEFAULT '{}',
  reason TEXT NOT NULL,
  confidence confidence_level NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(fit_assessment_id, requirement_id)
);

CREATE TABLE eligibility_evidence (
  eligibility_assessment_id UUID NOT NULL REFERENCES eligibility_assessments(id) ON DELETE CASCADE,
  evidence_id UUID NOT NULL REFERENCES evidence(id),
  PRIMARY KEY(eligibility_assessment_id, evidence_id)
);

CREATE TABLE risks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  fit_assessment_id UUID REFERENCES fit_assessments(id) ON DELETE CASCADE,
  profile_id UUID NOT NULL REFERENCES student_profiles(id) ON DELETE CASCADE,
  program_id UUID REFERENCES programs(id) ON DELETE CASCADE,
  risk_type TEXT NOT NULL,
  severity risk_severity NOT NULL,
  title TEXT NOT NULL,
  reason TEXT NOT NULL,
  recommended_action TEXT,
  status risk_status NOT NULL DEFAULT 'OPEN',
  confidence confidence_level NOT NULL DEFAULT 'MEDIUM',
  detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at TIMESTAMPTZ
);

CREATE TABLE risk_evidence (
  risk_id UUID NOT NULL REFERENCES risks(id) ON DELETE CASCADE,
  evidence_id UUID NOT NULL REFERENCES evidence(id),
  PRIMARY KEY(risk_id, evidence_id)
);

CREATE TABLE strategy_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  profile_id UUID NOT NULL REFERENCES student_profiles(id) ON DELETE CASCADE,
  research_plan_id UUID REFERENCES research_plans(id),
  status run_status NOT NULL DEFAULT 'RUNNING',
  scoring_version TEXT NOT NULL,
  strategy_version TEXT NOT NULL,
  summary TEXT,
  plan_health_score NUMERIC(5,2),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ
);

CREATE TABLE application_plans (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  strategy_run_id UUID NOT NULL REFERENCES strategy_runs(id) ON DELETE CASCADE,
  profile_id UUID NOT NULL REFERENCES student_profiles(id) ON DELETE CASCADE,
  program_id UUID NOT NULL REFERENCES programs(id),
  category program_category NOT NULL,
  priority INTEGER NOT NULL,
  fit_assessment_id UUID REFERENCES fit_assessments(id),
  rationale TEXT NOT NULL,
  estimated_cost JSONB NOT NULL DEFAULT '{}',
  next_deadline DATE,
  next_action TEXT,
  status TEXT DEFAULT 'PLANNED',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(strategy_run_id, program_id)
);

CREATE TABLE roadmap_tasks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  strategy_run_id UUID NOT NULL REFERENCES strategy_runs(id) ON DELETE CASCADE,
  profile_id UUID NOT NULL REFERENCES student_profiles(id) ON DELETE CASCADE,
  program_id UUID REFERENCES programs(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  description TEXT,
  task_type TEXT NOT NULL,
  due_date DATE,
  priority INTEGER DEFAULT 3,
  status task_status NOT NULL DEFAULT 'TODO',
  evidence_ids JSONB NOT NULL DEFAULT '[]',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ
);

CREATE TABLE saved_programs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  profile_id UUID NOT NULL REFERENCES student_profiles(id) ON DELETE CASCADE,
  program_id UUID NOT NULL REFERENCES programs(id),
  notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(profile_id, program_id)
);

CREATE TABLE monitor_subscriptions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  profile_id UUID NOT NULL REFERENCES student_profiles(id) ON DELETE CASCADE,
  program_id UUID REFERENCES programs(id) ON DELETE CASCADE,
  field_key TEXT NOT NULL,
  frequency TEXT NOT NULL DEFAULT 'WEEKLY',
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  last_checked_at TIMESTAMPTZ,
  next_check_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE monitor_snapshots (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  subscription_id UUID NOT NULL REFERENCES monitor_subscriptions(id) ON DELETE CASCADE,
  field_key TEXT NOT NULL,
  old_value JSONB,
  new_value JSONB,
  change_type TEXT NOT NULL,
  evidence_ids JSONB NOT NULL DEFAULT '[]',
  material_change BOOLEAN NOT NULL DEFAULT FALSE,
  checked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE counterfactual_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  profile_id UUID NOT NULL REFERENCES student_profiles(id) ON DELETE CASCADE,
  base_strategy_run_id UUID REFERENCES strategy_runs(id),
  scenario_name TEXT NOT NULL,
  modified_profile JSONB NOT NULL,
  result JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  profile_id UUID NOT NULL REFERENCES student_profiles(id) ON DELETE CASCADE,
  document_type TEXT NOT NULL,
  status task_status NOT NULL DEFAULT 'TODO',
  expires_at DATE,
  notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_programs_country_field ON programs(country_code, field_of_study);
CREATE INDEX idx_programs_normalized_name ON programs(normalized_name);
CREATE INDEX idx_requirements_program ON requirements(program_id);
CREATE INDEX idx_evidence_subject ON evidence(subject_type, subject_id);
CREATE INDEX idx_evidence_retrieved ON evidence(retrieved_at DESC);
CREATE INDEX idx_search_runs_user ON search_runs(user_id, requested_at DESC);
CREATE INDEX idx_risks_profile_status ON risks(profile_id, status, severity);
CREATE INDEX idx_strategy_profile ON strategy_runs(profile_id, created_at DESC);
CREATE INDEX idx_tasks_profile_due ON roadmap_tasks(profile_id, due_date, status);
CREATE INDEX idx_monitor_next_check ON monitor_subscriptions(next_check_at) WHERE enabled = TRUE;

-- updated_at trigger
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END; $$ LANGUAGE plpgsql;

CREATE TRIGGER users_updated_at BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER profiles_updated_at BEFORE UPDATE ON student_profiles FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER preferences_updated_at BEFORE UPDATE ON profile_preferences FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER institutions_updated_at BEFORE UPDATE ON institutions FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER programs_updated_at BEFORE UPDATE ON programs FOR EACH ROW EXECUTE FUNCTION set_updated_at();
