// ── What is left of the home's `?view=` (FEAT-104, reversed) ──
//
// FEAT-104 mounted a fleet overview under `/` and made it what a bare `/`
// means. That was the wrong shape twice over: the home stopped being the
// conversation every link, notification and reflex in this product had meant
// since FEAT-077, and a screen worth its own address was reachable only by a
// query parameter nothing in the nav spelled. The overview is `/fleet` now — an
// ordinary route beside `/floor` — and `/` is the chat, always, with no switch
// in it to read.
//
// So this module is one function: the old spelling still lives in bookmarks and
// in notification payloads no release can rewrite, and something has to
// recognise it long enough to forward it. When those URLs have aged out, this
// file goes with them.
//
// Nothing here fetches and nothing here renders (the ARCH-300 split).

/**
 * Where a legacy `/?view=fleet` should land, or `null` if the URL is not one.
 *
 * Only `view` is dropped; everything else rides along untouched. A URL that
 * reached the old overview could carry anything beside `view` — a server chip,
 * a filter, whatever a future row links with — and a redirect that swallowed
 * the rest would turn a working bookmark into a subtly wrong page rather than
 * an obviously broken one.
 *
 * `?view=` with anything else after it is not this URL: `?view=chat` asked for
 * what `/` already is, and `?view=now` is the agent workspace's grammar on the
 * wrong path. Both stay on the home, which is where landing on the home is the
 * right answer.
 */
export function legacyFleetPath(
  search: string | URLSearchParams,
): string | null {
  const params =
    typeof search === "string"
      ? new URLSearchParams(search)
      : new URLSearchParams(search);
  if (params.get("view") !== "fleet") return null;
  params.delete("view");
  const rest = params.toString();
  return rest ? `/fleet?${rest}` : "/fleet";
}
