/**
 * The canonical encoding of a vault's private config, and its hash.
 *
 * The browser's half of a two-implementation contract. The other is
 * `condor/vault_config.py`; they are held to the same vector
 * (`canonical.test.ts` and `tests/test_vault_config.py` share a digest), and if
 * they ever disagree, every vault on chain becomes unverifiable and nobody
 * finds out until a run refuses to start.
 *
 * This exists so the browser can check what it is about to sign. Condor hands
 * back a config and says "this hashes to what the chain carries"; a signer who
 * takes that on trust has given up the only guarantee the design offers, which
 * is that not even Condor can run a vault on parameters its runner did not
 * sign.
 *
 * The rules, and why each is a rule rather than a preference:
 *
 * - **JSON, UTF-8, no whitespace.** One serialization, no formatting to agree on.
 * - **Object keys sorted at every depth**, by code point. Both languages keep
 *   insertion order, so two clients that built the same config in a different
 *   order would otherwise hash differently.
 * - **Array order preserved.** An array is data; sorting it would change meaning.
 * - **Non-integer numbers refused.** `0.1` has no single shortest form across
 *   languages, and a config that hashes differently on two machines is worse
 *   than one that will not hash at all.
 * - **`null` refused.** "Absent" and "present but null" are the same intent and
 *   two different hashes.
 */

export class NotCanonical extends Error {}

function check(value: unknown, path: string): void {
  if (typeof value === "boolean" || typeof value === "string") return;
  if (typeof value === "number") {
    if (!Number.isInteger(value)) {
      throw new NotCanonical(
        `${path || "config"} is ${value}; use a string or an integer — ` +
          "non-integers do not have one shortest form across languages",
      );
    }
    return;
  }
  if (value === null || value === undefined) {
    throw new NotCanonical(
      `${path || "config"} is ${value === null ? "null" : "undefined"}; leave the key out instead — ` +
        "absent and null are the same intent and two different hashes",
    );
  }
  if (Array.isArray(value)) {
    value.forEach((item, index) => check(item, `${path}[${index}]`));
    return;
  }
  if (typeof value === "object") {
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      check(item, path ? `${path}.${key}` : key);
    }
    return;
  }
  throw new NotCanonical(`${path || "config"} is a ${typeof value}, which has no canonical form`);
}

function encode(value: unknown): string {
  if (typeof value === "string") return JSON.stringify(value);
  if (typeof value === "boolean" || typeof value === "number") return String(value);
  if (Array.isArray(value)) return `[${value.map(encode).join(",")}]`;
  const entries = Object.entries(value as Record<string, unknown>)
    // Code-point order, which is what Python's `sort_keys` uses too. A locale
    // comparison here would sort "Z" before "a" on some machines and not others.
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([key, item]) => `${JSON.stringify(key)}:${encode(item)}`);
  return `{${entries.join(",")}}`;
}

/** The exact bytes that get hashed. */
export function canonicalJson(config: Record<string, unknown>): Uint8Array {
  if (config === null || typeof config !== "object" || Array.isArray(config)) {
    throw new NotCanonical("the config must be an object");
  }
  check(config, "");
  return new TextEncoder().encode(encode(config));
}

/** `sha256` of the canonical encoding, lowercase hex — what goes on chain. */
export async function configHash(config: Record<string, unknown>): Promise<string> {
  // `.buffer` rather than the view: WebCrypto wants a BufferSource, and the
  // encoder always returns a fresh, exactly-sized array.
  const digest = await crypto.subtle.digest("SHA-256", canonicalJson(config).buffer as ArrayBuffer);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
