/**
 * The invariant sessionState.ts claims about itself, made checkable.
 *
 * That module says it is the single definition site for every key this app
 * persists, and the SEC-231 session boundary leans on it: `clearSessionState`
 * can only sort a key into CLEARED or KEPT if the key's name passes through the
 * module at all. The claim used to be prose — ten keys were defined at their
 * writers while the header said that could not happen, and nothing failed. This
 * test is what makes it true tomorrow rather than only today.
 *
 * It is a source scan, not a runtime check, because the failure it catches is a
 * key that is *never* read by the boundary: no amount of exercising
 * `clearSessionState` can see a name it was never told about.
 *
 * Adding a key means declaring it in sessionState.ts under CLEARED or KEPT and
 * importing it at the writer. That is the whole point — the decision gets
 * recorded next to the others instead of being made silently in a component.
 */

import { describe, expect, it } from "vitest";

/**
 * Every module's source, read through Vite rather than `node:fs`: the app's
 * tsconfig carries no node types, and a glob relative to this file keeps
 * working wherever the suite is run from.
 */
const SOURCES: Record<string, string> = import.meta.glob("../**/*.{ts,tsx}", {
  query: "?raw",
  import: "default",
  eager: true,
});

/**
 * The credential trio, which is not sorted by the session boundary: `logout`
 * drops all three unconditionally, and auth-token.ts stays dependency-free so
 * the API client can read a token without importing this module. Every other
 * exemption belongs in sessionState.ts instead of here.
 */
const ALLOWED = new Set([
  "lib/sessionState.ts",
  "lib/auth.ts",
  "lib/auth-token.ts",
]);

/**
 * Glob keys are relative to *this* file: a sibling comes back as `./auth.ts`,
 * anything else as `../pages/Dex.tsx`. Re-root both on `src/` so the allowlist
 * and the failure messages read as paths someone can open.
 */
function underSrc(key: string): string {
  return key.startsWith("../") ? key.slice(3) : `lib/${key.replace(/^\.\//, "")}`;
}

/** Every non-test source module, as a `[path under src/, source]` pair. */
const FILES: [file: string, source: string][] = Object.entries(SOURCES)
  .map(([key, source]): [string, string] => [underSrc(key), source])
  .filter(
    ([file]) =>
      !/\.test\.tsx?$/.test(file) &&
      !file.endsWith(".d.ts") &&
      !ALLOWED.has(file),
  );

/** `localStorage.getItem(<arg>)` and its two siblings, capturing the argument. */
const STORAGE_CALL = /localStorage\.(?:get|set|remove)Item\(\s*([^,)\s]+)/g;

/** A module-scope `const NAME = "literal"` — column 0, so locals do not count. */
const MODULE_CONST =
  /^(?:export )?const ([A-Za-z_$][\w$]*)(?:\s*:[^=]+)?\s*=\s*(["'])(.*?)\2\s*;?\s*$/gm;

function moduleConsts(source: string): Map<string, string> {
  const found = new Map<string, string>();
  for (const m of source.matchAll(MODULE_CONST)) found.set(m[1], m[3]);
  return found;
}

describe("sessionState is the single definition site for persisted keys", () => {
  it("finds the modules it is meant to be scanning", () => {
    // A broken glob would make every assertion below pass vacuously.
    expect(FILES.map(([file]) => file)).toContain("lib/page-context.ts");
    expect(FILES.length).toBeGreaterThan(100);
  });

  it("has no module passing a bare string to localStorage", () => {
    const offenders: string[] = [];
    for (const [file, source] of FILES) {
      for (const m of source.matchAll(STORAGE_CALL)) {
        if (/^["'`]/.test(m[1])) {
          offenders.push(`${file}: localStorage(${m[1]}…)`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it("has no module declaring a storage key of its own", () => {
    // A SCREAMING_SNAKE name containing KEY, bound to a string literal: the
    // shape every one of the ten relocated strays had.
    const offenders: string[] = [];
    for (const [file, source] of FILES) {
      for (const [name, value] of moduleConsts(source)) {
        if (/^[A-Z0-9_]+$/.test(name) && name.includes("KEY")) {
          offenders.push(`${file}: const ${name} = "${value}"`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it("has no module reading localStorage through a key it spelled itself", () => {
    // The escape hatch from the rule above: a key whose name dodges the
    // convention but is still handed straight to localStorage.
    const offenders: string[] = [];
    for (const [file, source] of FILES) {
      const declared = moduleConsts(source);
      for (const m of source.matchAll(STORAGE_CALL)) {
        const value = declared.get(m[1]);
        if (value !== undefined) {
          offenders.push(`${file}: const ${m[1]} = "${value}"`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});
