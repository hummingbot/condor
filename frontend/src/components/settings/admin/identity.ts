/**
 * What to call a person, on the client (FEAT-088).
 *
 * The server sends `display_name` already resolved, and that is what a row
 * renders. This module exists for the cases where there is no server answer to
 * render yet — a row rebuilt while a mutation is in flight — and for the parts
 * of the identity a name alone does not carry: the handle, and the initials the
 * avatar shows.
 *
 * The ladder is the same one `user_display_name` applies in `config_manager.py`,
 * deliberately: two copies of a naming rule drift into two different names for
 * one person, and the person notices before the developer does.
 */

import type { AdminPerson } from "@/lib/admin-api";

/** The subset of a person this module needs — so a partial row can use it too. */
export type Named = Pick<
  AdminPerson,
  "user_id" | "first_name" | "last_name" | "username"
> &
  Partial<Pick<AdminPerson, "display_name">>;

/** Full name, else the handle, else the id. Never empty. */
export function displayName(p: Named): string {
  if (p.display_name) return p.display_name;
  const full = [p.first_name, p.last_name].filter(Boolean).join(" ").trim();
  if (full) return full;
  if (p.username) return p.username;
  return `User ${p.user_id}`;
}

/** `@handle`, or an empty string when Telegram never gave us one. */
export function handle(p: Named): string {
  return p.username ? `@${p.username}` : "";
}

/**
 * Up to two initials for the avatar.
 *
 * Falls back to `??` rather than to a letter of the id: an id has no initials,
 * and a digit in an avatar reads as a name we got wrong rather than as a name
 * we never had.
 */
export function initials(p: Named): string {
  const name = displayName(p);
  if (name.startsWith("User ")) return "??";
  const parts = name.split(/\s+/).filter(Boolean);
  const letters = parts.slice(0, 2).map((part) => part[0]);
  return letters.join("").toUpperCase() || "??";
}

/** "1 Mar 2026", for the dates that are a fact rather than a duration. */
export function formatDate(epochSeconds: number): string {
  if (!epochSeconds) return "";
  return new Date(epochSeconds * 1000).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/** How the role reads in the UI, including the id that holds a grant and nothing else. */
export function roleLabel(person: AdminPerson): string {
  if (!person.known) return "not a user";
  return person.role || "unknown";
}
