/**
 * Prefilled GitHub issue links.
 *
 * The dashboard files no issues itself. It builds the URL of GitHub's own new-
 * issue form with the fields already filled in, and the user submits it under
 * their own account — so the report is attributed to the person who hit the
 * bug, they get the reply notifications, and this install holds no token that
 * could write to the tracker.
 *
 * The query keys are the `id`s of the fields in `.github/ISSUE_TEMPLATE/*.yml`.
 * Renaming a field there silently drops its prefill here (GitHub ignores keys
 * it does not recognize), so the two files move together.
 *
 * This is the one place a draft becomes a github.com URL, so it is where the
 * secret shapes get masked — every field, whoever wrote it. A caller that
 * assembles its own text (the crash boundary hands over a raw message and
 * stack) cannot forget to mask it, and a future third caller inherits the
 * control by construction.
 */

import { redact } from "@/lib/diagnostics";
import type { Area } from "@/lib/page-context";

export const ISSUE_REPO = "hummingbot/condor";

/**
 * GitHub answers 414 to a request line a few kilobytes past this, and browsers
 * and proxies have their own limits below that. Staying well under it means a
 * long report degrades by losing its diagnostics tail, not by failing to open.
 */
const MAX_URL_LEN = 6000;

export type IssueKind = "bug" | "feature";

export interface IssueDraft {
  kind: IssueKind;
  title: string;
  /** Which part of Condor — matched against the template's dropdown labels. */
  area?: Area;
  /** What happened (bug) or what to build (feature). */
  description: string;
  /** Steps to reproduce (bug) or why it matters (feature). */
  detail?: string;
  /** The block from `buildDiagnostics`, when the user chose to include it. */
  diagnostics?: string;
  /** A stack trace or log excerpt. Bug template only — features have no such field. */
  logs?: string;
}

interface FieldMap {
  template: string;
  description: string;
  detail: string;
  /** Where the diagnostics go — the first field trimmed when the URL is long. */
  extra: string;
  /** Free-form logs. Absent on the feature template, which has no such field. */
  logs?: string;
}

const FIELDS: Record<IssueKind, FieldMap> = {
  bug: {
    template: "bug_report.yml",
    description: "what-happened",
    detail: "reproduce",
    extra: "attachment",
    logs: "logs",
  },
  feature: {
    template: "feature_request.yml",
    description: "feature-suggestion",
    detail: "feature-impact",
    extra: "feature-additional-context",
  },
};

const TRUNCATED = "\n…(truncated)";

/** Drop `key`'s tail until the whole URL fits; delete the field if it empties. */
function trimField(
  params: URLSearchParams,
  key: string,
  url: () => string,
  floor: number,
) {
  if (!params.has(key)) return;
  let value = params.get(key) ?? "";
  while (url().length > MAX_URL_LEN && value.length > floor) {
    value = value.slice(0, Math.max(floor, value.length - 400));
    if (!value.trim()) {
      params.delete(key);
      return;
    }
    params.set(key, value + TRUNCATED);
  }
}

/**
 * Trim, then mask, then hand to the URL.
 *
 * Masking runs before `trimField` measures anything, so the length budget is
 * computed over the text that will actually be sent — a mask that shortens a
 * field must not leave the URL trimmed for bytes it no longer carries.
 */
function outgoing(value: string): string {
  return redact(value.trim());
}

export function buildIssueUrl(draft: IssueDraft): string {
  const fields = FIELDS[draft.kind];
  const base = `https://github.com/${ISSUE_REPO}/issues/new`;
  const params = new URLSearchParams();

  params.set("template", fields.template);
  if (draft.title.trim()) params.set("title", outgoing(draft.title));
  // GitHub matches a prefilled dropdown by its label. A value the template does
  // not offer is ignored, leaving the field for the user to pick — so a stale
  // label degrades to a manual choice, never to a broken link.
  if (draft.area) params.set("area", draft.area);
  params.set(fields.description, outgoing(draft.description));
  if (draft.detail?.trim()) params.set(fields.detail, outgoing(draft.detail));
  if (draft.diagnostics?.trim()) params.set(fields.extra, outgoing(draft.diagnostics));
  if (fields.logs && draft.logs?.trim()) params.set(fields.logs, outgoing(draft.logs));

  const url = () => `${base}?${params.toString()}`;

  // Cut the machine-written parts to nothing before touching a word the user
  // wrote, cheapest first: the environment block is short and reconstructible,
  // a stack trace is not, and both keep their head — where the answer is. Only
  // if the prose alone is oversized does it get trimmed, and never below a
  // length that still describes the problem.
  trimField(params, fields.extra, url, 0);
  if (fields.logs) trimField(params, fields.logs, url, 0);
  trimField(params, fields.detail, url, 0);
  trimField(params, fields.description, url, 500);

  return url();
}

/**
 * Open the prefilled form. Called from a click handler so the popup blocker
 * treats it as user-initiated; `noopener` keeps the new tab from reaching back
 * into this one.
 */
export function openIssueDraft(draft: IssueDraft) {
  window.open(buildIssueUrl(draft), "_blank", "noopener,noreferrer");
}
