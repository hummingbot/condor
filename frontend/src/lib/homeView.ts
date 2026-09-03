// ── The home route, as rules (FEAT-104) ──
//
// `/` mounts two things now: the conversation it has been since FEAT-077, and
// a fleet overview that answers "what are my agents doing?" without being
// asked. Which one is on screen is a query parameter, exactly as the agent
// workspace spells its section (`workspace/views.ts`) — one route, its state
// written down in the URL, and the reading of that URL in a module rather than
// in JSX.
//
// The reason this is a file and not three `pathname === "/"` checks in
// `AppShell` is the flip. Step 3 of FEAT-104 makes the overview the default,
// and that is deliberately one revertible commit: with the grammar here it is a
// change to `DEFAULT_HOME_VIEW` and nothing else, because ⌘K, the full-bleed
// rule, the keys-overlay exemption and the chat's own route facts all ask this
// module rather than the pathname.
//
// Nothing here fetches and nothing here renders (the ARCH-300 split).

/** Every value `/?view=` can take. */
export const HOME_VIEWS = ["chat", "fleet"] as const;

export type HomeViewId = (typeof HOME_VIEWS)[number];

/**
 * The view a bare `/` opens on.
 *
 * Still the chat. FEAT-104 steps 1 and 2 mount the overview and build it; step
 * 3 — the habit change, and the whole risk of the feature — flips this constant
 * once somebody has lived with the overview at `/?view=fleet` and decided.
 */
export const DEFAULT_HOME_VIEW: HomeViewId = "chat";

export function isHomeView(id: string | null | undefined): id is HomeViewId {
  return !!id && (HOME_VIEWS as readonly string[]).includes(id);
}

/**
 * Which view of the home a query string names.
 *
 * A `?view=` this route does not own — a workspace section pasted onto the
 * wrong path, a typo — reads as the default rather than as an error page. The
 * home is where people land, and landing nowhere is worse than landing on the
 * default.
 */
export function homeView(search: string | URLSearchParams): HomeViewId {
  const params =
    typeof search === "string" ? new URLSearchParams(search) : search;
  const named = params.get("view");
  return isHomeView(named) ? named : DEFAULT_HOME_VIEW;
}

/**
 * The shortest URL that lands on a home view.
 *
 * The default is never spelled out, which is the same rule the workspace
 * applies to `view=now`: the shortest URL that lands somewhere is the one
 * people paste. It is also what makes ⌘K survive the flip untouched — today it
 * resolves to `/`, and after step 3 to `/?view=chat`, with no edit here or at
 * the call site.
 */
export function homePath(view: HomeViewId): string {
  return view === DEFAULT_HOME_VIEW ? "/" : `/?view=${view}`;
}

/**
 * Home views that lay themselves out edge to edge and scroll inside their own
 * panes, so `main` gives them no padding and no scrollbar of its own.
 *
 * Both of them, for two different reasons: the chat owns the full viewport and
 * scrolls its transcript, and the overview is a screen-tall list that scrolls
 * itself. Written as a list rather than as `true` because the answer belongs to
 * the view — a third one arriving with an ordinary padded body should be able
 * to say so here instead of in the shell.
 */
export const FULL_BLEED_HOME_VIEWS: readonly HomeViewId[] = ["chat", "fleet"];
