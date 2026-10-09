"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { ErrorNote, LoadingNote, Section, fmtDate } from "../components/ui";
import { createDocument, listDocuments, updateDocument, type DocumentItem } from "../lib/api";
import {
  downloadDocumentFile,
  isMissingResource,
  isRouteUnavailable,
  removeDocumentFile,
  uploadDocumentFile,
  uploadRejection,
} from "../lib/api-extra";
import type { StrategiesQuery } from "./sections-shared";


/* ------------------------------------------------ Application Readiness */

/** MASTER_SPEC §17 — the eight documents we track (backend stores free-text
 *  document_type, so each row matches by normalised alias). */
const DOC_TYPES: { key: string; label: string; aliases: string[] }[] = [
  { key: "transcript", label: "Academic transcript", aliases: ["transcript", "academic_transcript"] },
  { key: "passport", label: "Passport", aliases: ["passport"] },
  {
    key: "language_score",
    label: "Language score report",
    aliases: ["language_score", "ielts", "toefl", "pte", "language"],
  },
  { key: "cv", label: "CV / résumé", aliases: ["cv", "resume"] },
  {
    key: "sop",
    label: "Statement of purpose",
    aliases: ["sop", "statement_of_purpose", "motivation_letter"],
  },
  {
    key: "lors",
    label: "Recommendation letters",
    aliases: ["lors", "lor", "recommendation_letters", "recommendations", "letters_of_recommendation"],
  },
  { key: "portfolio", label: "Portfolio", aliases: ["portfolio"] },
  {
    key: "financial_proof",
    label: "Financial proof",
    aliases: ["financial_proof", "financial_documents", "bank_statement", "fund_proof"],
  },
];

const DOC_STATUS_COPY: Record<string, { label: string; cls: string }> = {
  DONE: { label: "Ready", cls: "chip-good" },
  IN_PROGRESS: { label: "In progress", cls: "chip-warn" },
  TODO: { label: "Not started", cls: "chip-neutral" },
  BLOCKED: { label: "Blocked", cls: "chip-bad" },
  SKIPPED: { label: "Skipped", cls: "chip-neutral" },
};

function normType(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]/g, "");
}

function docStatusCopy(status: string) {
  return DOC_STATUS_COPY[status] ?? { label: status, cls: "chip-neutral" };
}

/* ------------------------------------------------------- document files */

/** Per-document upload/download state (P2-15). `available: false` records
 *  that the backend file routes are not deployed on this server yet, so every
 *  row can settle into a graceful "not available" state after one probe. */
type DocFileState = {
  busy: boolean;
  progress: number | null;
  error: string | null;
  notice: string | null;
  hasFile: boolean;
  available: boolean;
};

const DOC_FILE_IDLE: DocFileState = {
  busy: false,
  progress: null,
  error: null,
  notice: null,
  hasFile: false,
  available: true,
};

function DocRow({
  label,
  doc,
  fallbackType,
  today,
  busy,
  onSetStatus,
  onAdd,
  fileState,
  onUpload,
  onDownload,
  onRemove,
}: {
  label: string;
  doc: DocumentItem | null;
  fallbackType: string;
  today: string | null;
  busy: boolean;
  onSetStatus: (id: string, status: string) => void;
  onAdd: (documentType: string) => void;
  fileState: DocFileState;
  onUpload: (docId: string, file: File) => void;
  onDownload: (docId: string) => void;
  onRemove: (docId: string) => void;
}) {
  const status = doc?.status ?? "TODO";
  const copy = docStatusCopy(status);
  const expired = !!(doc?.expires_at && today && doc.expires_at.slice(0, 10) < today);
  // A file picked but not yet uploaded is validated client-side first, so an
  // obviously wrong file never costs a request (mirrors the server rules).
  const [picked, setPicked] = useState<File | null>(null);
  const [pickIssue, setPickIssue] = useState<string | null>(null);
  const inputId = `file-${fallbackType}`;

  function handlePick(event: React.ChangeEvent<HTMLInputElement>): void {
    const file = event.target.files?.[0] ?? null;
    event.target.value = ""; // allow re-picking the same file name
    if (!file) return;
    const issue = uploadRejection(file);
    setPickIssue(issue);
    setPicked(issue ? null : file);
  }

  return (
    <li className="flex flex-wrap items-center gap-3 py-2 first:pt-0 last:pb-0">
      {doc ? (
        <input
          type="checkbox"
          id={`doc-${fallbackType}`}
          checked={status === "DONE"}
          disabled={busy}
          onChange={() => onSetStatus(doc.id, status === "DONE" ? "TODO" : "DONE")}
          aria-label={`Mark ${label} as ready`}
          className="h-4 w-4 shrink-0 accent-[#1D5C46] disabled:opacity-40"
        />
      ) : (
        <span aria-hidden className="h-4 w-4 shrink-0 rounded border border-line-dark" />
      )}
      {doc ? (
        <label
          htmlFor={`doc-${fallbackType}`}
          className="min-w-0 flex-1 cursor-pointer text-sm text-ink"
        >
          {label}
        </label>
      ) : (
        <span className="min-w-0 flex-1 text-sm text-ink-faint">{label}</span>
      )}
      {doc ? (
        <span className={`chip ${copy.cls}`}>{copy.label}</span>
      ) : (
        <button
          type="button"
          onClick={() => onAdd(fallbackType)}
          disabled={busy}
          className="btn-ghost btn-sm"
          aria-label={`Add ${label}`}
        >
          + Add
        </button>
      )}
      {doc?.expires_at && (
        <span className={`text-xs tabular-nums ${expired ? "text-danger" : "text-ink-faint"}`}>
          {expired ? "Expired " : "Expires "}
          {fmtDate(doc.expires_at)}
        </span>
      )}

      {/* P2-15 — file upload / download / remove (backend routes may not be
          deployed yet: `available: false` settles every row into a note). */}
      {doc &&
        (fileState.available ? (
          <span className="flex flex-wrap items-center gap-2">
            <label
              htmlFor={inputId}
              className="btn-ghost btn-sm cursor-pointer"
              title="PDF, PNG, JPG or DOCX — up to 10 MB"
            >
              Choose file…
              <input
                id={inputId}
                type="file"
                className="sr-only"
                accept=".pdf,.png,.jpg,.jpeg,.docx"
                disabled={fileState.busy}
                onChange={handlePick}
              />
            </label>
            <button
              type="button"
              className="btn-ghost btn-sm"
              disabled={fileState.busy || !picked}
              onClick={() => {
                if (picked) onUpload(doc.id, picked);
                setPicked(null);
              }}
              aria-label={`Upload file for ${label}`}
            >
              {fileState.busy && fileState.progress !== null
                ? `Uploading… ${fileState.progress}%`
                : fileState.busy
                  ? "Uploading…"
                  : "Upload"}
            </button>
            <button
              type="button"
              className="btn-ghost btn-sm"
              disabled={fileState.busy}
              onClick={() => onDownload(doc.id)}
              aria-label={`Download file for ${label}`}
            >
              Download
            </button>
            <button
              type="button"
              className="btn-ghost btn-sm"
              disabled={fileState.busy}
              onClick={() => onRemove(doc.id)}
              aria-label={`Remove file from ${label}`}
            >
              Remove
            </button>
            {picked && (
              <span className="max-w-[10rem] truncate text-xs text-ink-soft" title={picked.name}>
                {picked.name}
              </span>
            )}
          </span>
        ) : (
          <span className="text-xs text-ink-faint">
            File upload isn’t available on this server yet
          </span>
        ))}

      {(pickIssue || fileState.error) && (
        <span className="basis-full text-xs text-danger" role="alert">
          {pickIssue ?? fileState.error}
        </span>
      )}
      {!pickIssue && fileState.notice && (
        <span className="basis-full text-xs text-forest" role="status">
          {fileState.notice}
        </span>
      )}
    </li>
  );
}


export function DocumentsSection({
  today,
  strategies,
}: {
  today: string | null;
  strategies: StrategiesQuery;
}) {
  const queryClient = useQueryClient();
  const [docFiles, setDocFiles] = useState<Record<string, DocFileState>>({});

  const documents = useQuery({ queryKey: ["documents"], queryFn: listDocuments });

  const updateDoc = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) => updateDocument(id, status),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["documents"] }),
  });
  const addDoc = useMutation({
    mutationFn: (documentType: string) => createDocument(documentType),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["documents"] }),
  });

  /* ------------------------------------------------ document file ops (P2-15) */

  const fileStateFor = (docId: string): DocFileState => docFiles[docId] ?? DOC_FILE_IDLE;
  const patchFileState = (docId: string, patch: Partial<DocFileState>) =>
    setDocFiles((prev) => ({ ...prev, [docId]: { ...(prev[docId] ?? DOC_FILE_IDLE), ...patch } }));

  /** One failed route probe flips every row into the graceful not-available
   *  state (the endpoints may simply not be deployed on this server yet). */
  function settleFileError(docId: string, error: unknown): void {
    if (isRouteUnavailable(error)) {
      patchFileState(docId, { busy: false, progress: null, available: false });
      return;
    }
    if (isMissingResource(error)) {
      patchFileState(docId, {
        busy: false,
        progress: null,
        hasFile: false,
        error: "That document no longer exists — refresh the page to reload your checklist.",
      });
      return;
    }
    const raw = error instanceof Error ? error.message : "That request failed.";
    // FastAPI's plain 413/body-cap responses have no friendly envelope.
    const message = /^Request failed \(/.test(raw)
      ? "The server rejected that file — use a PDF, PNG, JPG or DOCX up to 10 MB."
      : raw;
    patchFileState(docId, { busy: false, progress: null, error: message });
  }

  async function uploadDocFile(docId: string, file: File): Promise<void> {
    patchFileState(docId, { busy: true, progress: 0, error: null, notice: null });
    try {
      await uploadDocumentFile(docId, file, (percent) =>
        patchFileState(docId, { progress: percent })
      );
      patchFileState(docId, {
        busy: false,
        progress: null,
        hasFile: true,
        error: null,
        notice: "File uploaded — the checklist refreshed to match.",
      });
      queryClient.invalidateQueries({ queryKey: ["documents"] });
    } catch (error) {
      settleFileError(docId, error);
    }
  }

  async function downloadDocFile(docId: string): Promise<void> {
    patchFileState(docId, { busy: true, error: null, notice: null });
    try {
      const blob = await downloadDocumentFile(docId);
      const url = URL.createObjectURL(blob);
      const opened = window.open(url, "_blank");
      if (!opened) {
        // Popup blocked: fall back to an anchor download in this tab.
        const a = document.createElement("a");
        a.href = url;
        a.rel = "noreferrer";
        document.body.appendChild(a);
        a.click();
        a.remove();
      }
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
      patchFileState(docId, { busy: false, hasFile: true });
    } catch (error) {
      if (isMissingResource(error)) {
        patchFileState(docId, {
          busy: false,
          notice: null,
          error: "No file has been uploaded for this document yet — choose one to upload.",
        });
        return;
      }
      settleFileError(docId, error);
    }
  }

  async function removeDocFile(docId: string): Promise<void> {
    patchFileState(docId, { busy: true, error: null, notice: null });
    try {
      await removeDocumentFile(docId);
      patchFileState(docId, {
        busy: false,
        hasFile: false,
        notice: "File removed — the checklist row stays.",
      });
      queryClient.invalidateQueries({ queryKey: ["documents"] });
    } catch (error) {
      if (isMissingResource(error)) {
        patchFileState(docId, { busy: false, hasFile: false, notice: "There was no file to remove." });
        return;
      }
      settleFileError(docId, error);
    }
  }

  const fileRoutesUnavailable = Object.values(docFiles).some((s) => !s.available);

  /* ------------------------------------------------ Application readiness */
  const readinessRows = useMemo(() => {
    const docItems = documents.data?.items ?? [];
    const remaining = [...docItems];
    const rows = DOC_TYPES.map((def) => {
      const idx = remaining.findIndex((d) =>
        def.aliases.some((alias) => normType(alias) === normType(d.document_type))
      );
      const doc = idx >= 0 ? remaining.splice(idx, 1)[0] : null;
      return { key: def.key, label: def.label, doc };
    });
    // Any other document type stored on the profile still gets a row.
    for (const extra of remaining) {
      rows.push({
        key: extra.document_type,
        label: extra.document_type.replaceAll("_", " "),
        doc: extra,
      });
    }
    return rows;
  }, [documents.data]);
  const readyCount = readinessRows.filter((r) => r.doc?.status === "DONE").length;


  return (
    <>

      {/* Application readiness */}
      {strategies.isSuccess && (
        <Section
          index="—"
          title="Application readiness"
          aside={
            <span className="text-xs text-ink-faint">
              {readyCount} of {DOC_TYPES.length} ready
            </span>
          }
        >
          {documents.isLoading && <LoadingNote what="Loading your document checklist…" />}
          {documents.isError && (
            <ErrorNote
              message={`Could not load your checklist: ${(documents.error as Error).message}. Nothing was changed — try again shortly.`}
            />
          )}
          {documents.isSuccess && (
            <ul className="flex flex-col divide-y divide-line">
              {readinessRows.map((row) => (
                <DocRow
                  key={row.key}
                  label={row.label}
                  doc={row.doc}
                  fallbackType={row.key}
                  today={today}
                  busy={updateDoc.isPending || addDoc.isPending}
                  onSetStatus={(id, status) => updateDoc.mutate({ id, status })}
                  onAdd={(documentType) => addDoc.mutate(documentType)}
                  fileState={row.doc ? fileStateFor(row.doc.id) : DOC_FILE_IDLE}
                  onUpload={uploadDocFile}
                  onDownload={downloadDocFile}
                  onRemove={removeDocFile}
                />
              ))}
            </ul>
          )}
          {fileRoutesUnavailable && (
            <p className="mt-3 text-xs text-ink-faint">
              File upload and download aren’t available on this server yet — the checklist
              still tracks your document status.
            </p>
          )}
          {updateDoc.isError && (
            <div className="mt-3">
              <ErrorNote
                message={`Could not update that document: ${(updateDoc.error as Error).message}`}
              />
            </div>
          )}
          {addDoc.isError && (
            <div className="mt-3">
              <ErrorNote
                message={`Could not add that document: ${(addDoc.error as Error).message}`}
              />
            </div>
          )}
          <p className="mt-3 text-xs text-ink-faint">
            Files you upload stay in your account (PDF/PNG/JPG/DOCX, up to 10 MB) — nothing
            here is ever sent to a university.
          </p>
        </Section>
      )}

    </>
  );
}
