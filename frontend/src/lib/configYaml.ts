import yaml from "js-yaml";

// Canonical key always hidden from the config→YAML view: `id` is the config's
// identifier, set out-of-band (URL / new-id field), never edited inline.
const ALWAYS_HIDDEN_KEYS = ["id"] as const;

// Extra read-only controller-identity keys. These are NOT stripped by default
// because most editors round-trip the YAML back to the server on save, and
// dropping them would lose `controller_name` / `controller_type`. Only the
// read-only / partial-update controller browser opts into hiding them.
export const CONTROLLER_HIDDEN_KEYS = ["controller_name", "controller_type"] as const;

/** The one message every editor shows for YAML that parses but isn't a mapping. */
export const YAML_NOT_A_MAPPING = "YAML must be a mapping (key: value)";

export type YamlMappingResult =
  | { ok: true; value: Record<string, unknown> }
  | { ok: false; error: string };

/**
 * Parse YAML that is required to be a mapping (the shape every config editor
 * round-trips). Never throws: a parse failure comes back as `ok: false` with the
 * first line of the js-yaml message, which is the human-readable part — the rest
 * is the source snippet the editor already shows.
 */
export function parseYamlMapping(text: string): YamlMappingResult {
  let parsed: unknown;
  try {
    parsed = yaml.load(text);
  } catch (e) {
    const message = e instanceof Error ? e.message : "";
    return { ok: false, error: message.split("\n")[0] || "Invalid YAML" };
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return { ok: false, error: YAML_NOT_A_MAPPING };
  }
  return { ok: true, value: parsed as Record<string, unknown> };
}

/**
 * Validate-only view of `parseYamlMapping`: `null` when `text` is a YAML
 * mapping, otherwise the message to show the user. This is what the editors
 * call on every keystroke.
 */
export function validateYamlMapping(text: string): string | null {
  const result = parseYamlMapping(text);
  return result.ok ? null : result.error;
}

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
