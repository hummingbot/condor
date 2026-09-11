/**
 * Condor's service worker (FEAT-083). It exists for one reason: a `push`
 * handler cannot live anywhere else.
 *
 * There is deliberately NO `fetch` handler here. Caching is already decided,
 * and correctly, by the server — see `_NO_CACHE` and `_HashedAssets` in
 * condor/web/app.py. A cache in here would be a second answer to "which build
 * is installed", and the two would drift: a shell served fresh on every load
 * asking for chunks a worker answers from a build ago is a version skew nobody
 * can see. That is an omission to defend in review, not one to fix.
 *
 * Vite copies `public/` verbatim into `dist/`, so this is served by the file arm
 * of the SPA catch-all with no route to add, at scope `/` because it sits at the
 * root, and with `no-cache` like every other unhashed file beside the shell — so
 * an updated worker is picked up on the next load.
 */

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch {
    // A push that is not our JSON is still a push worth showing: the browser
    // will fire the generic "site updated in the background" notice otherwise,
    // and that is worse than an untitled line of text.
    payload = { body: event.data ? event.data.text() : "" };
  }

  const title = payload.title || "Condor";
  event.waitUntil(
    self.registration.showNotification(title, {
      body: payload.body || "",
      icon: "/icon-192.png",
      badge: "/icon-192.png",
      // The bell entry's own id, so a duplicate delivery (a retry, two push
      // services for one browser) collapses onto the notice already showing
      // instead of stacking a second copy of it.
      tag: payload.id || undefined,
      data: { link: payload.link || "/" },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const link = (event.notification.data && event.notification.data.link) || "/";

  event.waitUntil(
    (async () => {
      const clients = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      for (const client of clients) {
        if (new URL(client.url).origin !== self.location.origin) continue;
        // Focus and message, never `openWindow`. This is the runtime twin of
        // the manifest's `launch_handler: navigate-existing` (FEAT-082): a
        // second window is a second live chat WebSocket for the same user, so
        // the open one is told where to go and the SPA router takes it there.
        await client.focus();
        client.postMessage({ type: "condor:navigate", link });
        return;
      }
      await self.clients.openWindow(link);
    })(),
  );
});
