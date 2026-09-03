import { useEffect, useState } from "react";

import { authFetch } from "@/lib/auth-token";

/**
 * An `<img src>` for a bearer-guarded image route.
 *
 * `Depends(get_current_user)` is `HTTPBearer`, and a plain `<img src>` has no
 * way to carry an `Authorization` header — the browser issues that request on
 * its own, with cookies and nothing else. So the bytes are *fetched* with the
 * token, turned into an object URL, and revoked when the component that asked
 * for them goes away.
 *
 * Returns `null` while the fetch is in flight, and for a `url` of `null` — which
 * is how a caller says "this one is already a local object URL, leave it alone".
 * A failed fetch also returns `null`, so a picture that cannot be read renders as
 * the placeholder its caller draws rather than as a broken-image glyph.
 */
export function useAuthedImage(url: string | null): string | null {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);

  useEffect(() => {
    // No cleanup and no state write: the previous effect's cleanup has already
    // dropped whatever was here, and the first render starts at `null`.
    if (!url) return;
    let revoked = false;
    let created: string | null = null;

    void (async () => {
      try {
        const res = await authFetch(url);
        if (!res.ok) return;
        const blob = await res.blob();
        // The unmount may have happened while the body was being read; creating
        // the URL then would leak it, since the cleanup has already run.
        if (revoked) return;
        created = URL.createObjectURL(blob);
        setObjectUrl(created);
      } catch {
        // A picture that cannot be loaded is not worth breaking a transcript
        // over. The caller renders its placeholder.
      }
    })();

    return () => {
      revoked = true;
      if (created) URL.revokeObjectURL(created);
      setObjectUrl(null);
    };
  }, [url]);

  return objectUrl;
}
