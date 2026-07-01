import yaml from "js-yaml";

// Canonical key always hidden from the config→YAML view: `id` is the config's
// identifier, set out-of-band (URL / new-id field), never edited inline.
const ALWAYS_HIDDEN_KEYS = ["id"] as const;

// Extra read-only controller-identity keys. These are NOT stripped by default
// because most editors round-trip the YAML back to the server on save, and
// dropping them would lose `controller_name` / `controller_type`. Only the
// read-only / partial-update controller browser opts into hiding them.
export const CONTROLLER_HIDDEN_KEYS = ["controller_name", "controller_type"] as const;

export interface ConfigToYamlOptions {
  /** Extra keys to strip in addition to `id` (e.g. CONTROLLER_HIDDEN_KEYS). */
  hiddenKeys?: readonly string[];
  /** Strip keys with a leading underscore (internal/computed fields). */
  stripUnderscore?: boolean;
  /** Sort object keys alphabetically in the output. Defaults to false. */
  sortKeys?: boolean;
}

/**
 * Serialize a config object to YAML, filtering internal / read-only keys.
 *
 * Default policy (round-trip-safe, used by the editable config editors): strip
 * only `id`, preserve key order, `lineWidth: -1`. Call sites that render a
 * read-only or partial-update view can pass `hiddenKeys` / `stripUnderscore` /
 * `sortKeys` to hide more.
 */
export function configToYaml(
  config: Record<string, unknown>,
  opts: ConfigToYamlOptions = {},
): string {
  const { hiddenKeys = [], stripUnderscore = false, sortKeys = false } = opts;
  const hidden = new Set<string>([...ALWAYS_HIDDEN_KEYS, ...hiddenKeys]);
  const filtered: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(config)) {
    if (hidden.has(k)) continue;
    if (stripUnderscore && k.startsWith("_")) continue;
    filtered[k] = v;
  }
  return yaml.dump(filtered, { lineWidth: -1, noRefs: true, sortKeys });
}
