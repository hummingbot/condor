/**
 * The unread count, on the dock icon (FEAT-082).
 *
 * Only an installed app has a badge to set: in a normal tab this is either
 * absent or a silent no-op, which is exactly right — the same call site serves
 * both, and the browser decides whether there is anywhere to put the number.
 *
 * Guarded twice over because it is driven by a 60s poll rather than by a click.
 * The API is missing in Firefox, and rejects rather than throwing in some
 * embedded contexts; either would surface as an unhandled rejection in a
 * component that is only rendering a bell, on a timer, with nothing the user
 * did to explain it.
 */
export function setAppBadge(unread: number): void {
  try {
    const nav = navigator as Navigator & {
      setAppBadge?: (n?: number) => Promise<void>;
      clearAppBadge?: () => Promise<void>;
    };
    if (unread > 0) void nav.setAppBadge?.(unread)?.catch(() => {});
    else void nav.clearAppBadge?.()?.catch(() => {});
  } catch {
    /* no badge here; the bell is still right */
  }
}
