/**
 * The generated face for an address — which style, and the SVG for a seed.
 *
 * Same address, same face, everywhere a wallet or a vault appears. That is the
 * whole point: a base58 string is unreadable and two of them look alike, so the
 * face is what someone actually recognises. The style is a per-browser
 * preference (Settings → Theme) over DiceBear's collections, which is also what
 * `~/condor-app` does — one look across both.
 *
 * **Every style loads on demand.** The 25 collections are 700 KB of JSON
 * together, which on a dashboard that already warns about its bundle would be
 * paid by every page for a picker most people open once. Each entry imports its
 * own chunk, the chosen one is fetched at startup, and the settings grid asks
 * for the rest when it mounts. An avatar whose style has not arrived yet
 * renders as nothing rather than as some other style's face — a placeholder
 * that changed shape a tick later would defeat the recognition this exists for.
 */
import { Avatar, Style } from "@dicebear/core";
import { useCallback, useSyncExternalStore } from "react";

import { AVATAR_STYLE_KEY } from "@/lib/sessionState";

type StyleDef = ConstructorParameters<typeof Style>[0];

export interface AvatarStyleEntry {
  id: string;
  label: string;
  /** Its own literal import: a variable specifier is not statically analysable,
   *  and the point of this list is one chunk per style. */
  load: () => Promise<{ default: unknown }>;
}

export const AVATAR_STYLES: AvatarStyleEntry[] = [
  { id: "planets", label: "Planets", load: () => import("@dicebear/styles/planets.json") },
  { id: "waves", label: "Waves", load: () => import("@dicebear/styles/waves.json") },
  { id: "marbles", label: "Marbles", load: () => import("@dicebear/styles/marbles.json") },
  { id: "glass", label: "Glass", load: () => import("@dicebear/styles/glass.json") },
  { id: "rings", label: "Rings", load: () => import("@dicebear/styles/rings.json") },
  { id: "shapes", label: "Shapes", load: () => import("@dicebear/styles/shapes.json") },
  { id: "stripes", label: "Stripes", load: () => import("@dicebear/styles/stripes.json") },
  { id: "thumbs", label: "Thumbs", load: () => import("@dicebear/styles/thumbs.json") },
  { id: "pixel-art", label: "Pixel art", load: () => import("@dicebear/styles/pixel-art.json") },
  { id: "identicon", label: "Identicon", load: () => import("@dicebear/styles/identicon.json") },
  { id: "loops", label: "Loops", load: () => import("@dicebear/styles/loops.json") },
  { id: "squircles", label: "Squircles", load: () => import("@dicebear/styles/squircles.json") },
  {
    id: "constellation",
    label: "Constellation",
    load: () => import("@dicebear/styles/constellation.json"),
  },
  { id: "landscape", label: "Landscape", load: () => import("@dicebear/styles/landscape.json") },
  { id: "line-face", label: "Line face", load: () => import("@dicebear/styles/line-face.json") },
  { id: "weave", label: "Weave", load: () => import("@dicebear/styles/weave.json") },
  { id: "patchwork", label: "Patchwork", load: () => import("@dicebear/styles/patchwork.json") },
  { id: "critters", label: "Critters", load: () => import("@dicebear/styles/critters.json") },
  { id: "clay", label: "Clay", load: () => import("@dicebear/styles/clay.json") },
  { id: "voxel-bot", label: "Voxel bot", load: () => import("@dicebear/styles/voxel-bot.json") },
  { id: "pixelbot", label: "Pixelbot", load: () => import("@dicebear/styles/pixelbot.json") },
  { id: "sprouts", label: "Sprouts", load: () => import("@dicebear/styles/sprouts.json") },
  { id: "voxel-art", label: "Voxel art", load: () => import("@dicebear/styles/voxel-art.json") },
  { id: "blobs", label: "Blobs", load: () => import("@dicebear/styles/blobs.json") },
  { id: "moods", label: "Moods", load: () => import("@dicebear/styles/moods.json") },
];

export const DEFAULT_AVATAR_STYLE = "planets";

const styles = new Map<string, Style>();
const pending = new Map<string, Promise<void>>();
const listeners = new Set<() => void>();

/** Bumped whenever a style finishes loading or the chosen one changes, so a
 *  `useSyncExternalStore` snapshot is a value rather than a rebuilt object. */
let revision = 0;

function notify() {
  revision += 1;
  for (const listener of listeners) listener();
}

function storedId(): string {
  try {
    const value = localStorage.getItem(AVATAR_STYLE_KEY);
    // An id this build no longer ships (a style dropped from the list) is not
    // an error worth throwing at someone opening the dashboard — it is a
    // preference that no longer means anything, so it reads as unset.
    if (value && AVATAR_STYLES.some((entry) => entry.id === value)) return value;
  } catch {
    // Private windows and blocked site data throw rather than return null.
  }
  return DEFAULT_AVATAR_STYLE;
}

let chosen = storedId();

/** Fetch a style's chunk once; resolves as soon as it is usable. */
export function loadAvatarStyle(id: string): Promise<void> {
  if (styles.has(id)) return Promise.resolve();
  const existing = pending.get(id);
  if (existing) return existing;
  const entry = AVATAR_STYLES.find((candidate) => candidate.id === id);
  if (!entry) return Promise.reject(new Error(`no avatar style "${id}"`));
  const task = entry.load().then((module) => {
    styles.set(id, new Style(module.default as StyleDef));
    pending.delete(id);
    notify();
  });
  pending.set(id, task);
  return task;
}

// The chosen style is wanted on the first paint of any page with a wallet on
// it, so it is asked for at startup rather than when the first avatar mounts.
void loadAvatarStyle(chosen).catch(() => {});

export function getAvatarStyleId(): string {
  return chosen;
}

export function setAvatarStyleId(id: string): void {
  if (!AVATAR_STYLES.some((entry) => entry.id === id)) {
    throw new Error(`no avatar style "${id}"`);
  }
  chosen = id;
  try {
    localStorage.setItem(AVATAR_STYLE_KEY, id);
  } catch {
    // A browser that refuses storage still gets the style for this session.
  }
  void loadAvatarStyle(id).catch(() => {});
  notify();
}

/** The SVG for a seed, or `null` while that style's chunk is still in flight. */
export function avatarSvg(seed: string, id: string = chosen): string | null {
  const style = styles.get(id);
  if (!style) {
    void loadAvatarStyle(id).catch(() => {});
    return null;
  }
  return new Avatar(style, { seed }).toString();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

const snapshot = () => revision;

/** Re-renders the caller when the chosen style changes or a style arrives. */
function useAvatarRevision(): number {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

export function useAvatarStyleId(): string {
  useAvatarRevision();
  return chosen;
}

/** The face for a seed, live: it appears when its style lands and changes with
 *  the preference. */
export function useAvatarSvg(seed: string, id?: string): string | null {
  useAvatarRevision();
  return avatarSvg(seed, id ?? chosen);
}

/** Load every style — for the picker, which shows all of them at once. */
export function useLoadAllAvatarStyles(): () => void {
  return useCallback(() => {
    for (const entry of AVATAR_STYLES) void loadAvatarStyle(entry.id).catch(() => {});
  }, []);
}
