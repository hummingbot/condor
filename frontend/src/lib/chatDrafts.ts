import { CHAT_DRAFT_KEY_PREFIX } from "@/lib/sessionState";

/**
 * A half-written message, kept while the composer is not on screen.
 *
 * The composer holds its text in component state, and every chat surface is
 * inside a route: opening the portfolio and coming back unmounts it, so a
 * paragraph someone was still writing was gone by the time they returned — the
 * one piece of state in the app the user had *authored* was also the only one
 * not persisted. The transcript survives because it lives on the server; a
 * draft has never been sent anywhere, so it has to survive here.
 *
 * Keyed per conversation rather than globally: the session strip switches the
 * composer between conversations without unmounting it, and one shared draft
 * would follow the reader into a chat they never typed it in.
 *
 * Text only. Attachments are `File` handles that cannot be serialised, and a
 * restored draft that silently dropped the picture beside it would be worse
 * than one that never came back.
 */

/** The draft filed under `key`, or `""` — an unreadable store reads as empty. */
export function readDraft(key: string | undefined): string {
  if (!key) return "";
  try {
    return localStorage.getItem(CHAT_DRAFT_KEY_PREFIX + key) ?? "";
  } catch {
    return "";
  }
}

/**
 * File `text` under `key`, or drop the entry when there is nothing left to
 * keep — sending clears the box, and a sent message must not come back as a
 * draft the next time the surface mounts.
 */
export function writeDraft(key: string | undefined, text: string): void {
  if (!key) return;
  try {
    if (text) localStorage.setItem(CHAT_DRAFT_KEY_PREFIX + key, text);
    else localStorage.removeItem(CHAT_DRAFT_KEY_PREFIX + key);
  } catch {
    // Storage disabled or full: losing a draft is bad, throwing on a keystroke
    // is worse.
  }
}
