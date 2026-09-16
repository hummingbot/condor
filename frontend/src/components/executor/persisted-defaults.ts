// ── Panel defaults that outlive the session ──
//
// Every executor config module remembers the same thing across page loads: the
// handful of its state's fields that describe how this user trades — side,
// size, leverage, the barriers they habitually set. The rest of the state
// belongs to the panel that is open (a picked field, a resolved pool, a price,
// the anchoring flag), so what is restored is a *whitelist*, never the blob.
//
// That merge was written out five times over — order, position, DCA and LP
// panels plus the grid — and being five copies is what let CORR-308 (a loaded
// object whose nested array was the exported constant's own, so resizing it
// rewrote the defaults) be fixed in one of them and stay broken in four. Here
// it is once, and the copy it hands back is structured rather than shallow, so
// no caller can reach the constant it was made from however deep it writes.

/**
 * The `defaults`, with the whitelisted `fields` of a previous session merged
 * over them, read from `storageKey`.
 *
 * Anything unreadable — storage denied, a corrupt blob, a payload that is not
 * an object — yields the defaults untouched: a remembered preference is a
 * convenience, and there is no state of storage worth failing a panel's first
 * render over. A key absent from the payload, or present but `undefined`, keeps
 * the default too, so a field added after the last save arrives with its new
 * default rather than as a hole.
 */
export function loadPersistedDefaults<T extends object>(
  storageKey: string,
  defaults: T,
  fields: readonly (keyof T)[],
): T {
  try {
    const merged = structuredClone(defaults);
    const raw = localStorage.getItem(storageKey);
    if (!raw) return merged;
    const saved = JSON.parse(raw) as Record<string, unknown>;
    for (const key of fields) {
      if (key in saved && saved[key as string] !== undefined) {
        (merged as Record<string, unknown>)[key as string] = saved[key as string];
      }
    }
    return merged;
  } catch {
    // A half-merged object is not the defaults, so this re-copies rather than
    // returning whatever the loop had got to when the payload turned out to be
    // something `in` could not be asked about.
    return structuredClone(defaults);
  }
}

/** Write just the whitelisted `fields` of `state` to `storageKey`. */
export function savePersistedDefaults<T extends object>(
  storageKey: string,
  state: T,
  fields: readonly (keyof T)[],
) {
  const toSave: Record<string, unknown> = {};
  for (const key of fields) toSave[key as string] = state[key];
  localStorage.setItem(storageKey, JSON.stringify(toSave));
}
