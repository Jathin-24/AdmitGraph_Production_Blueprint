/**
 * W14 — static content for the /visa page (frontend-only, no API).
 *
 * Honesty rules baked into this file (PLAN.md W14 + ground rule 5):
 *  - Every `OfficialSource.url` was fetched over HTTP and returned a 2xx
 *    response on the date in `LAST_CHECKED`. Never add a URL that was not
 *    actually resolved — deep-linking unverified pages is forbidden.
 *  - Numeric figures appear ONLY as `financialProof.figure`, which the page
 *    renders next to its official-source link and VERIFY_FIGURES_NOTE.
 *    Everything else about money is qualitative (forms and expectations,
 *    never invented amounts).
 *  - No "guaranteed approval", no processing-time promises, no invented
 *    fees. Timing notes are quoted only where the official source states
 *    them, and they render with that source linked.
 *  - Document lists are framed as "typically requested" — the mission or
 *    immigration authority's own checklist is always the binding one.
 */

/** ISO date the source URLs below were last fetched successfully (HTTP 2xx). */
export const LAST_CHECKED = "2026-10-09";

/** Rendered next to every numeric figure — required by the W14 brief. */
export const VERIFY_FIGURES_NOTE =
  "Figures change — verify this amount at the official source before you rely on it.";

export type CountryCode = "DE" | "NL" | "US" | "UK" | "CA" | "AU";

export interface OfficialSource {
  /** Human label shown next to the link (authority or publisher name). */
  label: string;
  /** A URL that returned HTTP 2xx when LAST_CHECKED was set. */
  url: string;
}

export interface DocumentItem {
  /** Stable id for the localStorage checklist — never reuse or rename
   *  casually, or a student's saved ticks silently reset. */
  id: string;
  label: string;
  /** Optional qualifier — used for "if requested" style honesty. */
  note?: string;
}

export interface FinancialFigure {
  /** Numeric amount quoted verbatim from `source` (with its unit). */
  amount: string;
  /** The exact official page the amount was read from. */
  source: OfficialSource;
}

export interface FundingTip {
  text: string;
  /** Official link when the tip cites one; null for generic advice. */
  source: OfficialSource | null;
}

export interface VisaCountry {
  code: CountryCode;
  name: string;
  /** Official name of the immigration category, as the authority calls it. */
  visaName: string;
  /** Typical student-residence flow — qualitative, no outcome promises. */
  steps: string[];
  stepsSources: OfficialSource[];
  /** How financial proof works: which FORMS are accepted, honestly. */
  financialProof: {
    intro: string;
    forms: string[];
    /** Optional figure — page always pairs it with source + verify note. */
    figure?: FinancialFigure;
  };
  financialSources: OfficialSource[];
  /** Interactive checklist (ids feed the localStorage persistence). */
  documents: DocumentItem[];
  documentSources: OfficialSource[];
  fundingTips: FundingTip[];
}

/* ------------------------------------------------------------------ DE */

const DE: VisaCountry = {
  code: "DE",
  name: "Germany",
  visaName: "National visa for study purposes (type D), then a residence permit for study",
  steps: [
    "Check whether you need a visa before travelling. Nationals of many countries must get a national (type D) visa before entry; some nationalities may apply for the residence permit after visa-free entry — the Federal Foreign Office FAQ lists which is which.",
    "Secure your study place first. With a formal admission you apply for the regular national visa; without one yet, German missions can issue a prospective-student visa so you can meet the admission requirements in Germany.",
    "Prepare your proof of financial resources (Finanzierungsnachweis), health insurance, and — if your programme requires it — a German language certificate.",
    "Book an appointment at the German mission abroad (embassy or consulate) responsible for your place of residence and submit the application there. Individual visa decisions are made by the missions, not by the Foreign Office in Berlin.",
    "After arrival: register your address locally, then apply at the local immigration authority (Ausländerbehörde) for the residence permit that lets you study.",
  ],
  stepsSources: [
    {
      label: "Study in Germany (DAAD) — Visa",
      url: "https://www.study-in-germany.de/en/plan-your-studies/requirements/visa/",
    },
    {
      label: "German Federal Foreign Office — Visa & Service",
      url: "https://www.auswaertiges-amt.de/en/visa-service",
    },
  ],
  financialProof: {
    intro:
      "Germany expects one document proving you can cover your living costs — the Finanzierungsnachweis. Which form is accepted is decided by the German mission in your country, so ask them before you move any money. The forms below are the ones the official Study in Germany portal lists as possible.",
    forms: [
      "Blocked account (Sperrkonto) opened at a German bank or an approved online provider, with the required amount deposited",
      "Declared support (Verpflichtungserklärung) from a person permanently resident in Germany, filed at their local immigration authority",
      "Bank guarantee from a bank",
      "Documents from your parents certifying their income and financial assets",
      "Scholarship award notification from a recognised scholarship provider",
    ],
    figure: {
      amount: "€11,904 for one year (the annual “Regelbedarf” most applicants must show)",
      source: {
        label: "Study in Germany (DAAD) — Proof of financing",
        url: "https://www.study-in-germany.de/en/plan-your-studies/requirements/proof-of-financing/",
      },
    },
  },
  financialSources: [
    {
      label: "Study in Germany (DAAD) — Proof of financing",
      url: "https://www.study-in-germany.de/en/plan-your-studies/requirements/proof-of-financing/",
    },
  ],
  documents: [
    { id: "passport", label: "Valid passport" },
    { id: "admission", label: "Admission or prospective-student confirmation from a German institution" },
    { id: "financial-proof", label: "Proof of financial resources (Finanzierungsnachweis)" },
    { id: "health-insurance", label: "Health insurance that is valid in Germany" },
    { id: "language", label: "Language certificate, if your programme requires one" },
    { id: "application-form", label: "Completed visa application form and passport photos" },
    { id: "appointment", label: "Appointment confirmation for the German mission abroad" },
  ],
  documentSources: [
    {
      label: "German Federal Foreign Office — Visa FAQ",
      url: "https://www.auswaertiges-amt.de/en/visa-service/buergerservice/faq",
    },
    {
      label: "Study in Germany (DAAD) — Visa",
      url: "https://www.study-in-germany.de/en/plan-your-studies/requirements/visa/",
    },
  ],
  fundingTips: [
    {
      text: "The DAAD runs a scholarship database for international students — search it before assuming you must self-fund.",
      source: { label: "DAAD — German Academic Exchange Service", url: "https://www.daad.de/en/" },
    },
    {
      text: "One document can do double duty: a scholarship notification or your parents' income proof counts both as funding evidence and as visa financial proof.",
      source: {
        label: "Study in Germany (DAAD) — Proof of financing",
        url: "https://www.study-in-germany.de/en/plan-your-studies/requirements/proof-of-financing/",
      },
    },
  ],
};

/* ------------------------------------------------------------------ NL */

const NL: VisaCountry = {
  code: "NL",
  name: "Netherlands",
  visaName: "Residence permit for study (most non-EU students collect an MVV long-stay visa first)",
  steps: [
    "Check whether you need an MVV (long-stay visa). EU/EEA and Swiss citizens do not need one; many other nationalities do.",
    "Get your admission to a Dutch institution — the IND ties your permit to the institution and programme that admitted you.",
    "The application is filed by your institution as an IND-recognised sponsor, or by you where the IND allows self-application. Your institution's international office normally handles this; ask them at offer stage what they need from you.",
    "If an MVV is required, collect it from the Dutch mission abroad within its validity window, then travel to the Netherlands.",
    "After arrival, register at your municipality and follow the IND / institution instructions to receive your residence permit.",
  ],
  stepsSources: [
    { label: "IND (Dutch immigration service) — Study", url: "https://www.ind.nl/en/study" },
  ],
  financialProof: {
    intro:
      "The IND requires proof that your tuition is covered and that you have sufficient funds for your stay. Which evidence applies depends on your situation and programme — the IND study page describes the requirements per study programme, so check the page that matches yours rather than a generic amount.",
    forms: [
      "Scholarship award letter covering your tuition and living costs",
      "Sponsor's bank statement or declaration of support",
      "Your own bank statements showing available funds",
      "Proof of tuition payment as requested by the IND / your institution",
    ],
  },
  financialSources: [
    { label: "IND (Dutch immigration service) — Study", url: "https://www.ind.nl/en/study" },
  ],
  documents: [
    { id: "passport", label: "Valid passport" },
    { id: "admission", label: "Admission letter from a Dutch institution" },
    { id: "tuition-paid", label: "Proof of tuition payment" },
    { id: "funds", label: "Proof of sufficient funds (scholarship letter or sponsor bank documents)" },
    { id: "forms", label: "Completed application forms (usually filed via your institution)" },
    {
      id: "tb-test",
      label: "Tuberculosis test certificate",
      note: "Only if the IND requires one for your nationality — check the IND study page",
    },
    {
      id: "civil-docs",
      label: "Civil-status documents the IND asks for (for example a birth certificate)",
      note: "Requested case by case",
    },
  ],
  documentSources: [
    { label: "IND (Dutch immigration service) — Study", url: "https://www.ind.nl/en/study" },
  ],
  fundingTips: [
    {
      text: "Your institution files most student MVV applications — ask its international office exactly which funding evidence the IND accepts before you move money anywhere.",
      source: { label: "IND (Dutch immigration service) — Study", url: "https://www.ind.nl/en/study" },
    },
    {
      text: "Because tuition and permit evidence are checked together, getting your funding documents right early avoids a stalled application.",
      source: { label: "IND (Dutch immigration service) — Study", url: "https://www.ind.nl/en/study" },
    },
  ],
};

/* ------------------------------------------------------------------ US */

const US: VisaCountry = {
  code: "US",
  name: "United States",
  visaName: "F-1 student visa (J-1 exchange visitor and M-1 vocational visas are the alternatives)",
  steps: [
    "Apply to and be admitted by a school certified to enrol international students — only a certified school can issue your immigration documents.",
    "Accept your place and receive your Form I-20 from the school.",
    "Pay the SEVIS I-901 fee, complete the DS-160 online visa application, and pay the visa application fee. Current amounts are published on the U.S. Department of State and Study in the States sites — check them there rather than trusting any copied number.",
    "Book and attend your visa interview at the U.S. embassy or consulate. Bring your I-20, DS-160 confirmation and fee receipts, and be ready to explain briefly why you want to study, how you will support yourself, and what you plan after your studies.",
    "If the visa is approved, travel and enter the United States in student status — carry your I-20, which you may be asked to show at the border.",
  ],
  stepsSources: [
    {
      label: "EducationUSA (U.S. Department of State) — Apply for your student visa",
      url: "https://educationusa.state.gov/your-5-steps-us-study/apply-your-student-visa",
    },
  ],
  financialProof: {
    intro:
      "The United States does not use one blocked-account number. Instead your school states the estimated cost of one year on your I-20, and at the interview you show how you will pay those costs. What counts as sufficient is judged per applicant — be ready to explain your funding clearly.",
    forms: [
      "Bank statements for you, your parents, or another sponsor covering the first year's costs",
      "Scholarship or funding award letters from your school or an external funder",
      "A sponsor's letter committing to cover your costs, alongside their funding evidence",
    ],
  },
  financialSources: [
    {
      label: "EducationUSA (U.S. Department of State) — Finance your studies",
      url: "https://educationusa.state.gov/your-5-steps-us-study/finance-your-studies",
    },
    {
      label: "EducationUSA (U.S. Department of State) — Apply for your student visa",
      url: "https://educationusa.state.gov/your-5-steps-us-study/apply-your-student-visa",
    },
  ],
  documents: [
    { id: "passport", label: "Passport valid for travel to the United States" },
    { id: "i-20", label: "Form I-20 issued by your school" },
    { id: "ds-160", label: "DS-160 confirmation page" },
    { id: "fee-receipts", label: "Fee payment confirmations (SEVIS I-901 and visa application fee)" },
    {
      id: "photo",
      label: "Visa photograph",
      note: "Only if the application asks you to bring one — DS-160 usually takes a digital upload",
    },
    { id: "academic-records", label: "Academic records: transcripts, test scores and your admission letter" },
    { id: "financial-evidence", label: "Financial evidence matching the costs on your I-20" },
  ],
  documentSources: [
    {
      label: "EducationUSA (U.S. Department of State) — Apply for your student visa",
      url: "https://educationusa.state.gov/your-5-steps-us-study/apply-your-student-visa",
    },
  ],
  fundingTips: [
    {
      text: "EducationUSA's Finance Your Studies step explains the aid landscape — competition for international financial aid is high, so start with each school's own funding page early.",
      source: {
        label: "EducationUSA (U.S. Department of State) — Finance your studies",
        url: "https://educationusa.state.gov/your-5-steps-us-study/finance-your-studies",
      },
    },
    {
      text: "Ask every school directly what aid is available to international students — offers differ per institution, and an award letter doubles as interview evidence.",
      source: {
        label: "EducationUSA (U.S. Department of State) — Finance your studies",
        url: "https://educationusa.state.gov/your-5-steps-us-study/finance-your-studies",
      },
    },
  ],
};

/* ------------------------------------------------------------------ UK */

const UK: VisaCountry = {
  code: "UK",
  name: "United Kingdom",
  visaName: "Student visa (replaced the Tier 4 (General) student visa)",
  steps: [
    "Confirm eligibility: an offer from a licensed student sponsor, enough money for the course and living costs, and the required English language ability.",
    "Apply online at the earliest allowed time — gov.uk states you can apply up to 6 months before your course starts when applying from outside the UK (3 months from inside).",
    "Pay the visa fee and the healthcare surcharge as part of the application (see the figures in the financial section below).",
    "Prove your identity as the application directs — you will be told whether that is a biometric appointment or the ID Check app.",
    "Wait for the decision. gov.uk states decisions usually take about 3 weeks for applications from outside the UK and about 8 weeks from inside.",
    "If granted, set up access to your eVisa through a UKVI account linked to your travel document, then travel — you can arrive up to 1 week before a course of 6 months or less, up to 1 month for longer courses.",
  ],
  stepsSources: [
    { label: "GOV.UK — Student visa", url: "https://www.gov.uk/student-visa" },
  ],
  financialProof: {
    intro:
      "GOV.UK requires you to show “enough money to support yourself and pay for your course”, and says the amount varies with your circumstances. The exact evidence and holding period depend on whether you have a sponsor or your own funds — read the Money you need section of the official guide for the current rules before collecting statements.",
    forms: [
      "Bank statements for you or your sponsor, kept for the holding period the current rules specify",
      "A sponsor's letter confirming they will support you (with their relationship to you)",
      "Tuition fee payment receipts and scholarship award letters",
    ],
    figure: {
      amount: "£558 visa application fee (from outside the UK, per person)",
      source: { label: "GOV.UK — Student visa", url: "https://www.gov.uk/student-visa" },
    },
  },
  financialSources: [
    { label: "GOV.UK — Student visa", url: "https://www.gov.uk/student-visa" },
  ],
  documents: [
    { id: "passport", label: "Current passport (and previous passports, if asked)" },
    { id: "cas", label: "Confirmation of Acceptance for Studies (CAS) from your licensed sponsor" },
    { id: "financial-evidence", label: "Financial evidence matching the money-you-need rules" },
    {
      id: "english",
      label: "Evidence of English language ability",
      note: "A recognised test certificate, unless you are exempt",
    },
    {
      id: "tb-test",
      label: "Tuberculosis test results",
      note: "Only if you are applying from a country where the test is required",
    },
    {
      id: "atas",
      label: "ATAS certificate",
      note: "Only if your course (typically masters or above in certain subjects) requires it",
    },
    { id: "ucas-docs", label: "Academic documents your CAS asks you to provide" },
  ],
  documentSources: [
    { label: "GOV.UK — Student visa", url: "https://www.gov.uk/student-visa" },
  ],
  fundingTips: [
    {
      text: "The living-cost figures you must show live on GOV.UK and are updated there — always copy them from the source, never from an old blog post.",
      source: { label: "GOV.UK — Student visa", url: "https://www.gov.uk/student-visa" },
    },
    {
      text: "Many UK universities publish their own international scholarships — ask your admissions office what exists for your course before ruling out funding.",
      source: null,
    },
  ],
};

/* ------------------------------------------------------------------ CA */

const CA: VisaCountry = {
  code: "CA",
  name: "Canada",
  visaName: "Study permit (with a provincial or territorial attestation letter where required)",
  steps: [
    "Get your documents ready: an acceptance letter from a designated learning institution (DLI), plus a provincial or territorial attestation letter if your programme needs one.",
    "Apply online for the study permit and pay the application fee — the current amount is listed on the official study permit page linked below.",
    "Provide biometrics and any other documents IRCC asks for after you submit.",
    "Wait for the decision. If approved, travel with your letter of introduction and documents — the officer at the port of entry makes the final call and issues the study permit when everything checks out.",
    "Study while meeting your permit conditions, and extend before it expires if your programme runs longer.",
  ],
  stepsSources: [
    {
      label: "Canada.ca (IRCC) — Study permit",
      url: "https://www.canada.ca/en/immigration-refugees-citizenship/services/study-canada/study-permit.html",
    },
  ],
  financialProof: {
    intro:
      "IRCC requires proof you can pay your tuition, support yourself and any accompanying family without working in Canada, and cover travel to and from Canada — for the first year of study, plus a plan for the rest of a longer programme. IRCC assesses both the amount and its source.",
    forms: [
      "Receipt showing first-year tuition and housing fees paid to your DLI",
      "Your bank statements for the past 6 months, with documentation of the source of the income",
      "A student or education loan from a bank",
      "Scholarship award letter or proof you are in a Government of Canada-funded programme",
      "A letter from whoever is funding you, stating their job, their relationship to you and the amount — with their ID or business registration",
      "A guaranteed investment certificate (GIC) or a Canadian bank account in your name",
    ],
    figure: {
      amount:
        "CAN$23,448 per year for a single applicant — living expenses, excluding tuition and transportation, for applications submitted on or after 1 September 2026",
      source: {
        label: "Canada.ca (IRCC) — Proof of financial support",
        url: "https://www.canada.ca/en/immigration-refugees-citizenship/services/study-canada/study-permit/get-documents/financial-support.html",
      },
    },
  },
  financialSources: [
    {
      label: "Canada.ca (IRCC) — Proof of financial support",
      url: "https://www.canada.ca/en/immigration-refugees-citizenship/services/study-canada/study-permit/get-documents/financial-support.html",
    },
  ],
  documents: [
    { id: "passport", label: "Passport or travel document" },
    { id: "acceptance", label: "Letter of acceptance from a designated learning institution (DLI)" },
    {
      id: "attestation",
      label: "Provincial or territorial attestation letter (PAL/TAL)",
      note: "If your programme requires one — the official page says when",
    },
    { id: "financial-support", label: "Proof of financial support (see the funding section above)" },
    {
      id: "medical",
      label: "Immigration medical examination",
      note: "Only if IRCC asks you to take one",
    },
    {
      id: "police",
      label: "Police certificate(s)",
      note: "Only if requested for your country of residence",
    },
    {
      id: "caq",
      label: "Quebec Acceptance Certificate (CAQ)",
      note: "Only if you will study in Quebec",
    },
  ],
  documentSources: [
    {
      label: "Canada.ca (IRCC) — Study permit",
      url: "https://www.canada.ca/en/immigration-refugees-citizenship/services/study-canada/study-permit.html",
    },
    {
      label: "Canada.ca (IRCC) — Proof of financial support",
      url: "https://www.canada.ca/en/immigration-refugees-citizenship/services/study-canada/study-permit/get-documents/financial-support.html",
    },
  ],
  fundingTips: [
    {
      text: "IRCC's proof-of-funds page spells out exactly which documents it accepts — note that a scholarship letter only counts for what it actually covers: a tuition-only scholarship must be backed up with separate living-cost proof.",
      source: {
        label: "Canada.ca (IRCC) — Proof of financial support",
        url: "https://www.canada.ca/en/immigration-refugees-citizenship/services/study-canada/study-permit/get-documents/financial-support.html",
      },
    },
    {
      text: "Keeping tuition receipts and six months of clean bank statements early saves a re-submission later — IRCC looks at both amount and source.",
      source: {
        label: "Canada.ca (IRCC) — Proof of financial support",
        url: "https://www.canada.ca/en/immigration-refugees-citizenship/services/study-canada/study-permit/get-documents/financial-support.html",
      },
    },
  ],
};

/* ------------------------------------------------------------------ AU */

const AU: VisaCountry = {
  code: "AU",
  name: "Australia",
  visaName: "Student visa (subclass 500)",
  steps: [
    "Choose a course registered for international students and get your Confirmation of Enrolment (CoE) from the provider.",
    "Create an ImmiAccount on the Department of Home Affairs website and lodge the Student visa (subclass 500) application online, paying the application charge.",
    "Arrange Overseas Student Health Cover (OSHC) for the required period and include the certificate with your application.",
    "Respond to the genuine-student requirement in the application, and provide character evidence or health examinations if the department asks for them.",
    "Track the decision in your ImmiAccount. If the visa is granted, travel with your CoE and OSHC cover — the grant notice lists the conditions that apply to you, including any work limits.",
  ],
  stepsSources: [
    {
      label: "Study Australia (Australian Government) — Your guide to visas",
      url: "https://studyaustralia.gov.au/en/plan-your-move/your-guide-to-visas",
    },
    {
      label: "Department of Home Affairs (immigration authority)",
      url: "https://www.homeaffairs.gov.au/",
    },
  ],
  financialProof: {
    intro:
      "The department wants evidence you have enough money for travel, course fees and living costs, plus acceptable health insurance (OSHC). The required amount depends on your course and circumstances — the official Study Australia visa guide and the Home Affairs site publish the current calculations, so use those rather than any figure copied elsewhere.",
    forms: [
      "Personal or family funds, shown through bank statements covering the required costs",
      "Scholarship or sponsor funding letters",
      "Evidence that your Overseas Student Health Cover (OSHC) is in place",
      "Income or employment evidence for whoever is funding you, if asked",
    ],
  },
  financialSources: [
    {
      label: "Study Australia (Australian Government) — Your guide to visas",
      url: "https://studyaustralia.gov.au/en/plan-your-move/your-guide-to-visas",
    },
    {
      label: "Department of Home Affairs (immigration authority)",
      url: "https://www.homeaffairs.gov.au/",
    },
  ],
  documents: [
    { id: "passport", label: "Passport valid for your stay" },
    { id: "coe", label: "Confirmation of Enrolment (CoE) from your provider" },
    { id: "oshc", label: "Overseas Student Health Cover (OSHC) certificate" },
    { id: "financial-evidence", label: "Financial evidence for course fees, travel and living costs" },
    { id: "english", label: "English language test result, or evidence you are exempt" },
    { id: "academic-records", label: "Academic transcripts and certificates supporting your application" },
    {
      id: "character",
      label: "Police clearance / character documents",
      note: "Only if requested",
    },
    {
      id: "health-checks",
      label: "Health examinations or biometrics",
      note: "Only if requested through your ImmiAccount",
    },
  ],
  documentSources: [
    {
      label: "Study Australia (Australian Government) — Your guide to visas",
      url: "https://studyaustralia.gov.au/en/plan-your-move/your-guide-to-visas",
    },
    {
      label: "Department of Home Affairs (immigration authority)",
      url: "https://www.homeaffairs.gov.au/",
    },
  ],
  fundingTips: [
    {
      text: "Study Australia lists scholarships for international students, and most providers publish their own — check both before assuming the full cost is on you.",
      source: {
        label: "Study Australia (Australian Government)",
        url: "https://studyaustralia.gov.au/",
      },
    },
    {
      text: "Whatever funding you claim must be provable in the visa application — award letters and statements beat verbal promises.",
      source: {
        label: "Department of Home Affairs (immigration authority)",
        url: "https://www.homeaffairs.gov.au/",
      },
    },
  ],
};

/** All supported destination countries, in selector order. */
export const COUNTRIES: VisaCountry[] = [DE, NL, US, UK, CA, AU];

/** The internal funding cross-link every country's funding section shows. */
export const SCHOLARSHIPS_HREF = "/scholarships";
