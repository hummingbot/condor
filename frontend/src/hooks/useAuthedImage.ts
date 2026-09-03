import { useEffect, useState } from "react";

import { authFetch } from "@/lib/auth-token";

/** Where the bytes are: still coming, here, or never arriving. */
export type AuthedImageStatus = "loading" | "ready" | "error";

export interface AuthedImage {
  /** An object URL to hand an `<img src>`, or `null` unless `status` is "ready". */
  src: string | null;
  status: AuthedImageStatus;
}

/**
 * An `<img src>` for a bearer-guarded image route.
 *
 * `Depends(get_current_user)` is `HTTPBearer`, and a plain `<img src>` has no
 * way to carry an `Authorization` header — the browser issues that request on
 * its own, with cookies and nothing else. So the bytes are *fetched* with the
 * token, turned into an object URL, and revoked when the component that asked
 * for them goes away. This is the only place in the app that does that dance.
 *
 * Loading and failed are reported apart, because they want different pictures:
 * a caller reserves the image's box for the first (so the content below does
 * not jump when the bytes land) and collapses to nothing for the second, rather
 * than leaving a broken-image glyph on screen.
 *
 * A `url` of `null` is how a caller says "this one is already a local object URL
 * for bytes in this tab, leave it alone" — the composer's optimistic bubble. It
 * reports `"ready"` with no `src`: there is nothing to wait for.
 */
export function useAuthedImage(url: string | null): AuthedImage {
  // Keyed by url so a url change derives back to "loading" without a state
  // write from the effect body. `objectUrl === null` on a settled entry means
  // the fetch failed.
  const [state, setState] = useState<{ url: string; objectUrl: string | null } | null>(null);

  useEffect(() => {
    if (!url) return;
    let revoked = false;
    let created: string | null = null;

    void (async () => {
      try {
        const res = await authFetch(url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        // The unmount may have happened while the body was being read; creating
        // the URL then would leak it, since the cleanup has already run.
        if (revoked) return;
        created = URL.createObjectURL(blob);
        setState({ url, objectUrl: created });
      } catch {
        // A picture that cannot be loaded is not worth breaking a transcript
        // over. The caller renders nothing.
        if (revoked) return;
        setState({ url, objectUrl: null });
      }
    })();

    return () => {
      revoked = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [url]);

  if (!url) return { src: null, status: "ready" };
  const settled = state?.url === url ? state : null;
  if (!settled) return { src: null, status: "loading" };
  return settled.objectUrl
    ? { src: settled.objectUrl, status: "ready" }
    : { src: null, status: "error" };
}
