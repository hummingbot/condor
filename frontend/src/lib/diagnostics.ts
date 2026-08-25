/**
 * What a report should carry besides the prose.
 *
 * A report that says "it broke on the bots page" costs a round trip to find out
 * which build, which browser, and what the console said. This module keeps a
 * small ring of the last errors the session saw and renders them — together
 * with the build identity from `/api/v1/meta/env` and the page state from
 * `page-context.ts` — as a block the user can read in full before it goes
 * anywhere. Nothing is sent from here: the caller pastes the result into a
 * GitHub issue form (see `github-issue.ts`).
 */

import { SECRET_KEY_NAMES } from "@/lib/credential-fields";
import {
  type Area,
  describePage,
  displayPrefs,
  failingRequests,
  liveDataStatus,
} from "@/lib/page-context";

export interface AppEnv {
  version: string;
  branch: string;
  python: string;
  os: string;
  arch: string;
  in_docker: boolean;
}

const MAX_ENTRIES = 8;
const MAX_ENTRY_LEN = 300;

interface Entry {
  at: number;
  text: string;
}

const buffer: Entry[] = [];
let installed = false;

/**
 * `private_key` has to catch `privateKey` and `private-key` too, so each `_` in
 * a name becomes an optional separator. That makes the run-together spellings
 * the backend lists (`apikey`, `privatekey`) redundant, so they are dropped
 * here rather than compiled into a branch that can never win.
 */
const KEY_NAMES = (() => {
  const seen = new Set<string>();
  return SECRET_KEY_NAMES.filter((name) => {
    const flat = name.replace(/_/g, "");
    if (seen.has(flat)) return false;
    seen.add(flat);
    return true;
  }).map((name) => name.split("_").join("[_-]?"));
})();

/**
 * `name: value` — a credential name, then an actual assignment, then the value.
 *
 * The separator must contain a `:` or `=`. Whitespace alone used to qualify,
 * which was tolerable while this only saw console output but is not now that
 * `buildIssueUrl` runs it over prose the user typed: "my token expired
 * yesterday" came out as "my token *** yesterday". A key name is allowed a
 * trailing word (`secret_key`, `api_token_id`), and the separator may absorb an
 * auth scheme so `Authorization: Basic dXNlcjpwYXNz` masks the credential
 * rather than the word "Basic".
 */
const SECRET_ASSIGNMENT = new RegExp(
  // The names are plain lowercase words; nothing here needs escaping.
  `((?:${KEY_NAMES.join("|")})\\w*["'\\s]*[:=]["'\\s]*(?:(?:bearer|basic|token)\\s+)?)[^\\s"',}]{6,}`,
  "gi",
);

/**
 * `mnemonic: <twelve words>` — a secret whose value contains spaces.
 *
 * Every other rule here stops the value at the first space, which for a seed
 * phrase masks one word and prints the other eleven — worse than useless, since
 * eleven known words are trivially completed. These names get a value that runs
 * to the closing quote or the end of the line instead.
 *
 * Bare `seed` is deliberately not one of them: it is a wallet phrase only when
 * spelled out (`seed_phrase`), and on its own it is far more often a shuffle or
 * backtest seed, where swallowing the rest of the line would eat the sentence
 * around it. It still gets the single-token masking every other name gets.
 */
const SECRET_PHRASE =
  /((?:mnemonic|(?:seed|secret|recovery)[_-]?phrase)\w*["'\s]*[:=]["'\s]*)[^"'\n,}]{6,}/gi;

/**
 * `https://host/v2/<key>` — an RPC/API key carried as a bare path segment.
 *
 * Alchemy and Infura put the key in the path, not in a query parameter, so the
 * rule above never sees a name to match on. Anchoring to a `/v<n>/` segment of
 * a real URL keeps it off this app's own routes: what follows `/api/v1/` is
 * always a short lowercase noun, and the segment here must be long *and* carry
 * a digit. Wallet addresses and transaction hashes — public, and the signal a
 * bug report needs — never sit directly after a version segment.
 */
const URL_PATH_KEY = /(\/\/[^\s/"']+\/(?:[\w.-]+\/)*v\d+\/)(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{16,}/g;

/**
 * Mask the shapes that carry a secret.
 *
 * A console error can quote a request that carried a key. The user reviews the
 * block before submitting, but review is not a control you rely on for
 * credentials — the obvious shapes get masked first, and the review catches
 * what is left.
 *
 * Exported because masking belongs to *leaving the app*, not to this module:
 * `buildIssueUrl` runs it over every field it puts in a github.com URL. The
 * substitutions are idempotent — none of them matches its own output — so a
 * block masked here and masked again on the way out is unchanged.
 */
export function redact(text: string): string {
  return text
    .replace(/(bearer\s+)[\w.\-+/=]{8,}/gi, "$1***")
    .replace(SECRET_PHRASE, "$1***")
    .replace(SECRET_ASSIGNMENT, "$1***")
    .replace(URL_PATH_KEY, "$1***")
    .replace(/\beyJ[\w-]{10,}\.[\w-]+\.[\w-]+/g, "***jwt***");
}

function stringify(value: unknown): string {
  if (typeof value === "string") return value;
  if (value instanceof Error) return `${value.name}: ${value.message}`;
  try {
    return JSON.stringify(value) ?? String(value);
  } catch {
    return String(value);
  }
}

function record(text: string) {
  const clean = redact(text).replace(/\s+/g, " ").trim().slice(0, MAX_ENTRY_LEN);
  if (!clean) return;
  // A render loop can emit the same line hundreds of times; keeping one copy
  // leaves room in the ring for the errors that came before it.
  const last = buffer[buffer.length - 1];
  if (last && last.text === clean) {
    last.at = Date.now();
    return;
  }
  buffer.push({ at: Date.now(), text: clean });
  if (buffer.length > MAX_ENTRIES) buffer.shift();
}

/**
 * Start recording. Call once, before the app renders.
 *
 * `console.error` is wrapped rather than replaced: the original is always
 * invoked, so the devtools output is exactly what it was without this.
 */
export function installErrorRecorder() {
  if (installed || typeof window === "undefined") return;
  installed = true;

  const original = console.error;
  console.error = (...args: unknown[]) => {
    try {
      record(args.map(stringify).join(" "));
    } catch {
      // Recording must never be the reason an error goes unlogged.
    }
    original.apply(console, args as []);
  };

  window.addEventListener("error", (e) => {
    const where = e.filename ? ` (${e.filename}:${e.lineno})` : "";
    record(`${e.message}${where}`);
  });

  window.addEventListener("unhandledrejection", (e) => {
    record(`Unhandled rejection: ${stringify(e.reason)}`);
  });
}

/** The recorded errors, oldest first, each stamped with a local wall clock. */
export function recentErrors(): string[] {
  return buffer.map(
    (e) => `[${new Date(e.at).toTimeString().slice(0, 8)}] ${e.text}`,
  );
}

/** Fenced so GitHub renders the block as written — single newlines and all. */
function fence(body: string): string {
  return "```\n" + body + "\n```";
}

/**
 * The block that goes in the issue.
 *
 * A bug and an idea need different evidence. A bug gets everything the app
 * knows — build, page, live-data health, failing requests, console errors —
 * because any of it can be the answer. An idea gets the build and the page it
 * was had on, and nothing else: a stack trace under a feature request is noise
 * that makes the request look like a crash report.
 */
export function buildDiagnostics(opts: {
  kind: "bug" | "feature";
  /** Full route, path and query together — `pathname + search`. */
  route: string;
  server: string | null;
  env: AppEnv | null;
}): string {
  const { kind, server, env } = opts;
  const page = describePage(...splitRoute(opts.route));
  const lines: string[] = [];

  lines.push(
    env
      ? `Condor ${env.version} (${env.branch}) · Python ${env.python} · ${env.os}/${env.arch}${env.in_docker ? " · docker" : ""}`
      : "Condor version: unavailable",
  );
  lines.push(`Page: ${page.page}  [${page.route}]`);

  if (kind === "feature") return fence(lines.join("\n"));

  lines.push(`Server: ${server || "none selected"}`);
  lines.push(`Live data: websocket ${liveDataStatus()}`);
  lines.push(`Display: ${displayPrefs()}`);
  lines.push(`Browser: ${navigator.userAgent}`);
  lines.push(
    `Viewport: ${window.innerWidth}x${window.innerHeight} · TZ: ${Intl.DateTimeFormat().resolvedOptions().timeZone}`,
  );

  const failing = failingRequests();
  if (failing.length) {
    lines.push("", "Failing requests:");
    for (const line of failing) lines.push(`- ${redact(line)}`);
  }

  const errors = recentErrors();
  if (errors.length) {
    lines.push("", "Recent console errors:");
    for (const line of errors) lines.push(`- ${line}`);
  }

  return fence(lines.join("\n"));
}

/** `"/bots?tab=archived"` → `["/bots", "?tab=archived"]`. */
function splitRoute(route: string): [string, string] {
  const at = route.indexOf("?");
  return at === -1 ? [route, ""] : [route.slice(0, at), route.slice(at)];
}

/** The area the current page belongs to — the dialog's default selection. */
export function areaForRoute(route: string): Area {
  return describePage(...splitRoute(route)).area;
}
