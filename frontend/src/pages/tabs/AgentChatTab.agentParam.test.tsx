/**
 * `?agent=` opens whoever it names, every time — not just the first time
 * (Greptile, PR #314).
 *
 * `consumedAgentParam` used to be a one-shot `useRef(false)`: once the first
 * `?agent=` had been read and stripped from the URL, nothing ever flipped it
 * back, so a second "Start chatting" from the Hub — `/` -> `/hub` ->
 * `/?agent=<other>` — landed on a page that had already "used up" the only
 * read it was ever going to give the effect. `AgentChatTab` backs both `/`
 * and `/hub` (`App.tsx` routes them to the same `<Agents/>`/`<AgentChatTab/>`
 * element), so the instance — and its ref — survives the round trip; this
 * test exercises exactly that round trip.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

// A generic resolve-to-nothing for every `api.*` this tree happens to call
// (agents roster, delegations, fleet map, starters, …) — none of it is what
// this test is about, and every caller already falls back to `[]`/`undefined`
// when the query has not landed.
vi.mock(import("@/lib/api"), async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    api: new Proxy({}, { get: () => () => Promise.resolve(undefined) }) as typeof actual.api,
  };
});

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    user: { id: 1, username: "t", first_name: "T", role: "admin" },
    token: null,
    isAuthenticated: true,
    loginWithToken: vi.fn(),
    loginLocal: vi.fn(),
    logout: vi.fn(),
  }),
}));

// The Hub's own tree (Botcamp sign-in, the browse/publish tabs) is not what
// this test is about either — only that `AgentChatTab` renders *something*
// at `/hub` and keeps the same `consumedAgentParam` ref alive across the
// round trip to it.
vi.mock("@/pages/AgentHub", () => ({
  AgentHub: () => <div data-testid="agent-hub-stub" />,
}));

const startSession = vi.fn(
  (_key: string, _server: string | undefined, slug: string | undefined) =>
    `slot-${slug}-${startSession.mock.calls.length}`,
);

vi.mock("@/hooks/useChat", () => ({
  useChat: () => ({
    activeSlot: undefined,
    activeSlotId: null,
    slots: [],
    isSlotStreaming: () => false,
    isSlotQueued: () => false,
    permissionFor: () => null,
    permissionRequests: {},
    resolvePermission: vi.fn(),
    abortPrompt: vi.fn(),
    enablePrewarm: vi.fn(),
    destroySession: vi.fn(),
    resumeConversation: vi.fn(),
    setActiveSlotId: vi.fn(),
    sendMessage: vi.fn(),
    startSession,
    switchBrain: vi.fn(() => Promise.resolve()),
    switchServer: vi.fn(() => Promise.resolve()),
  }),
  useSessionOptions: () => ({
    agents: [],
    customProviders: [],
    agentBindings: [],
    defaultAgent: "claude-code",
  }),
}));

/** jsdom answers every query `false`; several panels turn on width. */
window.matchMedia = ((media: string) => ({
  matches: false,
  media,
  onchange: null,
  addEventListener: () => {},
  removeEventListener: () => {},
  addListener: () => {},
  removeListener: () => {},
  dispatchEvent: () => false,
})) as unknown as typeof window.matchMedia;

const { AgentChatTab } = await import("./AgentChatTab");

let container: HTMLDivElement;
let root: Root;
let navigateTo: (path: string) => void;

/** Carries `navigate` out to the test, the same way a click on the Hub's
 *  "Start chatting" would call it — `onOpenAgent={(slug) =>
 *  navigate(/?agent=...)}` in `AgentHub.tsx`. */
function Harness() {
  const navigate = useNavigate();
  useEffect(() => {
    navigateTo = navigate;
  }, [navigate]);
  return <AgentChatTab />;
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  startSession.mockClear();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

it("opens a second agent after the Hub, not just the first", async () => {
  const queryClient = new QueryClient();

  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/?agent=orca"]}>
          <Routes>
            <Route path="/" element={<Harness />} />
            <Route path="/hub" element={<Harness />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });

  // The landing `?agent=orca` opens Orca once.
  expect(startSession.mock.calls.map((c) => c[2])).toEqual(["orca"]);

  // Back to the Hub — same `AgentChatTab` instance, same ref.
  await act(async () => {
    navigateTo("/hub");
  });
  expect(document.querySelector('[data-testid="agent-hub-stub"]')).not.toBe(
    null,
  );

  // "Start chatting" on a second agent — the gesture `onOpenAgent` makes.
  await act(async () => {
    navigateTo("/?agent=sable");
  });

  // Sable opens too: the one-shot guard no longer eats every `?agent=` after
  // the first.
  expect(startSession.mock.calls.map((c) => c[2])).toEqual(["orca", "sable"]);
});
