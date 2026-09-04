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
// `AppShell` is the flip. Step 3 of FEAT-104 made the overview the default, and
// it stayed one revertible commit precisely because of the grammar here: ⌘K,
// the full-bleed rule and the chat's own route facts all ask this module rather
// than the pathname, so the flip is `DEFAULT_HOME_VIEW` and the handover rule
// below, and undoing it is the same two lines.
//
// Nothing here fetches and nothing here renders (the ARCH-300 split).

/** Every value `/?view=` can take. */
export const HOME_VIEWS = ["chat", "fleet"] as const;

export type HomeViewId = (typeof HOME_VIEWS)[number];

/**
 * The view a bare `/` opens on.
 *
 * The fleet overview, since FEAT-104 step 3 — the habit change the feature was
 * really about. `/` answers "what are my agents doing?" on load instead of
 * "what would you like to ask?", and the conversation is `/?view=chat`, one ⌘K
 * away from anywhere.
 *
 * If it has to go back it is this constant and the test that pins it: nothing
 * else in the app spells either view as a path.
 */
export const DEFAULT_HOME_VIEW: HomeViewId = "fleet";

export function isHomeView(id: string | null | undefined): id is HomeViewId {
  return !!id && (HOME_VIEWS as readonly string[]).includes(id);
}

/**
 * Parameters that are a request *to the conversation*, and so name the chat
 * without spelling `view=chat`.
 *
 * `?agent=` and `?ask=` (FEAT-092) are how an agent's page hands the chat a
 * question; `?conversation=` (FEAT-111) is how its Runs rail opens one. Every
 * one of them was written when a bare `/` meant the conversation, and they are
 * already in bookmarks and in notification payloads no release can rewrite.
 * Reading them here is what makes the step-3 flip safe: those links keep
 * meaning what they meant, with no edit at any call site.
 *
 * An empty one is not a request — `?agent=` with nothing after it is what a
 * half-built URL looks like, and the chat ignores it too. An explicit `?view=`
 * always wins: `/?view=fleet&agent=x` is somebody asking for the overview,
 * whatever else is riding along.
 */
const CHAT_HANDOVER_PARAMS = ["agent", "ask", "conversation"] as const;

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
  if (isHomeView(named)) return named;
  if (CHAT_HANDOVER_PARAMS.some((p) => params.get(p))) return "chat";
  return DEFAULT_HOME_VIEW;
}

/**
 * The shortest URL that lands on a home view.
 *
 * The default is never spelled out, which is the same rule the workspace
 * applies to `view=now`: the shortest URL that lands somewhere is the one
 * people paste. It is also what made ⌘K survive the flip untouched — it asks
 * for the chat by view, so it resolved to `/` before step 3 and to
 * `/?view=chat` after it, with no edit here or at the call site.
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
