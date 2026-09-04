/**
 * The one fetcher for a bearer-guarded image (ARCH-319).
 *
 * `<img src>` cannot carry an `Authorization` header, so the bytes have to be
 * fetched, turned into an object URL and released again — and there used to be
 * two copies of that dance, each missing something the other had. What these
 * cases pin is the union: an object URL never outlives the component that asked
 * for it (the routine chart is a rendered matplotlib image, so the in-flight
 * window is wide enough to click away from), loading and failed are told apart
 * so a caller can reserve a box for one and collapse for the other, and a `url`
 * of `null` is a pass-through for bytes the tab already holds — not a permanent
 * skeleton.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthedImage } from "./useAuthedImage";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const authFetch = vi.fn<(url: string) => Promise<Response>>();
vi.mock("@/lib/auth-token", () => ({
  authFetch: (url: string) => authFetch(url),
}));

let container: HTMLDivElement;
let root: Root;

// jsdom has no object-URL implementation.
const created: string[] = [];
const revoked: string[] = [];
let nextId = 0;

/** Renders what the hook reports, so a case can read it off the DOM. */
function Harness({ url }: { url: string | null }) {
  const image = useAuthedImage(url);
  return (
    <div id="out" data-status={image.status} data-src={image.src ?? ""} />
  );
}

const out = () => container.querySelector("#out") as HTMLDivElement;
const status = () => out().getAttribute("data-status");
const src = () => out().getAttribute("data-src");

async function render(url: string | null) {
  await act(async () => {
    root.render(<Harness url={url} />);
  });
}

/** A fetch whose body is only produced when the case says so. */
function deferred(): { resolve: (res: Response) => void } {
  let resolve!: (res: Response) => void;
  authFetch.mockReturnValueOnce(new Promise<Response>((r) => (resolve = r)));
  return { resolve };
}

/** jsdom's `Blob` and the platform `Response` do not interoperate, and the hook
 *  only ever reads `ok` and `blob()` — so that is the whole shape here. */
function png(): Response {
  return {
    ok: true,
    status: 200,
    blob: async () => new Blob([new Uint8Array(8)], { type: "image/png" }),
  } as unknown as Response;
}

function refused(): Response {
  return { ok: false, status: 404, blob: async () => new Blob([]) } as unknown as Response;
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  authFetch.mockReset();
  created.length = 0;
  revoked.length = 0;
  nextId = 0;
  URL.createObjectURL = vi.fn(() => {
    const url = `blob:test/${nextId++}`;
    created.push(url);
    return url;
  });
  URL.revokeObjectURL = vi.fn((url: string) => {
    revoked.push(url);
  });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  container.remove();
});

describe("useAuthedImage", () => {
  it("reserves the box while fetching, then hands over the object URL", async () => {
    const gate = deferred();
    await render("/api/v1/routines/instances/7/image");
    expect(status()).toBe("loading");
    expect(src()).toBe("");

    await act(async () => gate.resolve(png()));
    expect(status()).toBe("ready");
    expect(src()).toBe(created[0]);
    expect(authFetch).toHaveBeenCalledWith("/api/v1/routines/instances/7/image");

    await act(async () => root.unmount());
    expect(revoked).toEqual(created);
  });

  it("leaves nothing behind when it unmounts mid-fetch", async () => {
    const gate = deferred();
    await render("/api/v1/routines/instances/7/image");
    expect(status()).toBe("loading");

    // The click that went somewhere else, before the chart had rendered.
    await act(async () => root.unmount());
    await act(async () => gate.resolve(png()));

    // Either no URL was ever minted, or it was released — what must not happen
    // is a `blob:` that survives the component that asked for it.
    expect(created.filter((u) => !revoked.includes(u))).toEqual([]);
  });

  it("reports a refused fetch as failed, not as forever-loading", async () => {
    authFetch.mockResolvedValueOnce(refused());
    await render("/api/v1/routines/instances/9/image");
    await act(async () => {});
    expect(status()).toBe("error");
    expect(src()).toBe("");
    expect(created).toEqual([]);
  });

  it("reports a url of null as ready, and does not fetch it", async () => {
    // The composer's optimistic bubble: those bytes are already in this tab.
    await render(null);
    expect(status()).toBe("ready");
    expect(src()).toBe("");
    expect(authFetch).not.toHaveBeenCalled();
  });

  it("goes back to loading when the url changes, and releases the old bytes", async () => {
    const first = deferred();
    await render("/api/v1/routines/instances/1/image");
    await act(async () => first.resolve(png()));
    expect(status()).toBe("ready");
    const firstUrl = src();

    const second = deferred();
    await render("/api/v1/routines/instances/2/image");
    expect(status()).toBe("loading");
    expect(revoked).toEqual([firstUrl]);

    await act(async () => second.resolve(png()));
    expect(status()).toBe("ready");
    expect(src()).not.toBe(firstUrl);
  });
});
