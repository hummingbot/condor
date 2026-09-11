/**
 * The browser half of Web Push (FEAT-083).
 *
 * Registering the service worker, asking for permission, and turning the
 * browser's `PushSubscription` into the four fields `condor/push.py` stores.
 * Kept out of the Settings component so the component stays a component — the
 * only exports there are the pane itself.
 *
 * Everything here is guarded, because none of it exists on an insecure origin.
 * Condor is commonly reached over a tailnet at `http://host:8088`, where
 * `navigator.serviceWorker` is not merely restricted, it is *absent*: the
 * feature is not degraded there, it is missing, and `pushSupport()` is what
 * lets Settings say so instead of rendering a switch that cannot work.
 */

import { api } from "@/lib/api";

/** Why push is (not) available here. The Settings pane renders one per case. */
export type PushSupport =
  | "supported"
  /** `http://` on something other than localhost — the tailnet-by-IP case. */
  | "insecure-context"
  /** Secure, but this browser has no service worker or no PushManager. */
  | "unsupported";

export function pushSupport(): PushSupport {
  if (typeof window === "undefined") return "unsupported";
  // Order matters: on `http://` the two API checks below are false *because*
  // the context is insecure, and reporting "your browser cannot" there sends
  // the user to look in the wrong place entirely.
  if (!window.isSecureContext) return "insecure-context";
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    return "unsupported";
  }
  return "supported";
}

export function isPushSupported(): boolean {
  return pushSupport() === "supported";
}

/** The origin to name in the "you are on http://…" copy. */
export function currentOrigin(): string {
  return typeof window === "undefined" ? "" : window.location.origin;
}

/**
 * A name for this device in the Settings list. Best-effort by construction.
 *
 * Its only job is letting someone recognise which row is the laptop they no
 * longer have. It is never matched on — the endpoint is the identity — so a
 * wrong guess costs a confusing label and nothing else.
 */
export function deviceLabel(userAgent: string = navigator.userAgent): string {
  const browser =
    /Edg\//.test(userAgent) ? "Edge"
    : /OPR\//.test(userAgent) ? "Opera"
    : /Firefox\//.test(userAgent) ? "Firefox"
    : /Chrome\//.test(userAgent) ? "Chrome"
    : /Safari\//.test(userAgent) ? "Safari"
    : "Browser";
  const platform =
    /iPhone|iPad|iPod/.test(userAgent) ? "iOS"
    : /Android/.test(userAgent) ? "Android"
    : /Macintosh|Mac OS X/.test(userAgent) ? "macOS"
    : /Windows/.test(userAgent) ? "Windows"
    : /Linux/.test(userAgent) ? "Linux"
    : "";
  return platform ? `${browser} on ${platform}` : browser;
}

/** base64url (as the server sends it) → the bytes `subscribe()` wants.
 *
 * Backed by an explicit `ArrayBuffer` rather than `new Uint8Array(n)`: the DOM
 * types insist on an `ArrayBufferView<ArrayBuffer>` here, and the default
 * `ArrayBufferLike` could in principle be a `SharedArrayBuffer`.
 */
export function decodeVapidKey(base64: string): Uint8Array<ArrayBuffer> {
  const padded = base64.replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(padded + "=".repeat((4 - (padded.length % 4)) % 4));
  const bytes = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
  return bytes;
}

/** The browser's own key material, base64url, exactly as the server stores it. */
function encodeKey(subscription: PushSubscription, name: "p256dh" | "auth"): string {
  const key = subscription.getKey(name);
  if (!key) return "";
  let binary = "";
  for (const byte of new Uint8Array(key)) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function registration(): Promise<ServiceWorkerRegistration> {
  // Scope `/` because sw.js sits at the root and the click handler has to be
  // able to focus a window on any route.
  return navigator.serviceWorker.register("/sw.js", { scope: "/" });
}

/** This browser's current subscription, if it already has one. */
export async function currentSubscription(): Promise<PushSubscription | null> {
  if (!isPushSupported()) return null;
  const existing = await navigator.serviceWorker.getRegistration("/sw.js");
  if (!existing) return null;
  return existing.pushManager.getSubscription();
}

export class PermissionDeniedError extends Error {
  constructor() {
    // Not recoverable from here: once a user has said no, the browser will not
    // prompt again and a button that appears to try is a lie.
    super("Notifications are blocked for this site in the browser.");
    this.name = "PermissionDeniedError";
  }
}

/**
 * Turn desktop notifications on for this browser: permission, worker, server.
 *
 * The POST is what makes it real — a subscription the server never heard about
 * is a browser waiting for a push nobody will send.
 */
export async function subscribe(): Promise<void> {
  if (!isPushSupported()) throw new Error("Web Push is not available here.");

  const permission = await Notification.requestPermission();
  if (permission !== "granted") throw new PermissionDeniedError();

  const { public_key } = await api.getVapidKey();
  const reg = await registration();
  await navigator.serviceWorker.ready;

  const subscription = await reg.pushManager.subscribe({
    // Required by every browser that implements Push: a push must result in a
    // notification the user can see. Which is all this feature ever does.
    userVisibleOnly: true,
    applicationServerKey: decodeVapidKey(public_key),
  });

  await api.pushSubscribe({
    endpoint: subscription.endpoint,
    p256dh: encodeKey(subscription, "p256dh"),
    auth: encodeKey(subscription, "auth"),
    label: deviceLabel(),
  });
}

/**
 * Turn them off for this browser.
 *
 * The server is told first. If the local `unsubscribe()` were the first step
 * and the POST then failed, the row would live on and this device would keep
 * being pushed to with no way left to name the endpoint that has to go.
 */
export async function unsubscribe(): Promise<void> {
  const subscription = await currentSubscription();
  if (!subscription) return;
  await api.pushUnsubscribe(subscription.endpoint);
  await subscription.unsubscribe();
}

/**
 * Route the click on an OS notification into the SPA.
 *
 * `sw.js` focuses the window it found and posts the link rather than calling
 * `openWindow`, because a second window is a second live chat WebSocket for the
 * same user. This is the other end of that message. Returns its own unsubscribe.
 */
export function onPushNavigate(handler: (link: string) => void): () => void {
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) {
    return () => {};
  }
  const listener = (event: MessageEvent) => {
    const data = event.data as { type?: string; link?: string } | null;
    if (data?.type === "condor:navigate" && data.link) handler(data.link);
  };
  navigator.serviceWorker.addEventListener("message", listener);
  return () => navigator.serviceWorker.removeEventListener("message", listener);
}
