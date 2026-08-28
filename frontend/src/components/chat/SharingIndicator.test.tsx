/**
 * What the header chip promises (CORR-226).
 *
 * The chip is the compensating control for Always: the one surface that makes
 * an automatic share impossible to forget. That only holds if it is *true*, and
 * its truth is not its own to keep — every writer that withdraws a conversation
 * has to knock the chip's cached status over, or it goes on saying "Shared with
 * Condor" and offering "Not this one" for a chat the user already took back.
 *
 * There are two such writers, on two different pages, and the query carries no
 * refetch interval, so nothing else would correct them: the rail's share dialog
 * and the row in Settings → Privacy. Both are tested here alongside the reader
 * rather than in their own files, because the promise under test belongs to the
 * chip and neither writer can keep it alone.
 *
 * The fakes mirror the backend rather than the happy path: `unshare` clears the
 * receipt *and* sets excluded (condor/sharing/share.py:203, :245), so all three
 * fields the chip renders move at once — which is exactly why a stale entry is
 * a false statement about consent rather than a cosmetic lag.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ConversationSharingStatus,
  SharePreview,
  SharedConversation,
} from "@/lib/api";
import { ShareConversation } from "./ShareConversation";
import { SharingIndicator } from "./SharingIndicator";
import { SharingSettings } from "@/components/settings/SharingSettings";

const CHAT = "conv-1";

vi.mock("@/lib/api", () => ({
  api: {
    getSharingPreference: vi.fn(async () => ({
      state: "always",
      opted_in_at: 0,
      allowed: true,
      sweeping: true,
    })),
    getConversationSharing: vi.fn(async () => ({ ...STATUS })),
    setConversationExcluded: vi.fn(async () => ({ ...STATUS })),
    previewShare: vi.fn(async () => preview()),
    shareConversation: vi.fn(async () => {
      STATUS = { ...STATUS, shared: true, shared_at: "2026-08-26T00:00:00Z" };
      return { queued: true };
    }),
    // What the backend does: delete the copy, and refuse the chat for good.
    unshareConversation: vi.fn(async () => {
      STATUS = { ...STATUS, shared: false, shared_at: null, excluded: true };
      SHARES = [];
      return { ok: true };
    }),
    getSharingSettings: vi.fn(async () => ({
      enabled: true,
      env_overridden: false,
      can_change: true,
      endpoint_configured: true,
      pending: 0,
    })),
    listSharedConversations: vi.fn(async () => SHARES),
    setSharingEnabled: vi.fn(),
    setSharingPreference: vi.fn(),
    unshareEverything: vi.fn(),
  },
}));

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

// ── Fixtures ──

let STATUS: ConversationSharingStatus;
let SHARES: SharedConversation[];

function preview(): SharePreview {
  return {
    conversation_id: CHAT,
    title: "the SOL grid",
    surface: "web",
    agent_slug: "condor",
    agent_key: "condor",
    turns: [
      { role: "user", text: "hi", thought: "", tool_calls: [], kind: "", ts: 0 },
    ],
    counts: {},
    truncated: false,
    turns_omitted: 0,
    revision: 1,
    shared: STATUS.shared,
    share_id: "s-1",
    shared_at: STATUS.shared_at,
  };
}

// ── Harness ──

let container: HTMLDivElement;
let root: Root;

/** The chip and one writer under a single cache — the coupling being tested is
 *  an invalidation, which says nothing unless both share a QueryClient. */
async function render(writer: "dialog" | "settings") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <SharingIndicator conversationId={CHAT} />
        {writer === "dialog" ? (
          <ShareConversation conversationId={CHAT} open onClose={() => {}} />
        ) : (
          <SharingSettings />
        )}
      </QueryClientProvider>,
    );
  });
  await settle();
}

/** react-query resolves on a later macrotask than the render that asked, and an
 *  invalidation costs another round trip on top. */
async function settle() {
  for (let i = 0; i < 8; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

/** The chip's sentence, or "" when it renders nothing at all. */
const chip = () =>
  document.querySelector<HTMLElement>("[data-sharing-chip]")?.textContent ?? "";

/** Only the chip's sentence — `chip()` also carries the ⨯ button's label. */
const sentence = () =>
  document.querySelector<HTMLElement>("[data-sharing-chip] span")?.textContent ??
  "";

/** Which of the three states it is in, independent of the copy. */
const state = () =>
  document.querySelector<HTMLElement>("[data-sharing-chip]")?.dataset
    .sharingChip ?? "none";

async function click(label: string) {
  const button = [...document.querySelectorAll("button")].find((b) =>
    b.textContent?.trim().startsWith(label),
  );
  if (!button) throw new Error(`no "${label}" button rendered`);
  await act(async () => button.click());
  await settle();
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  STATUS = {
    conversation_id: CHAT,
    excluded: false,
    covered: true,
    shared: true,
    shared_at: "2026-08-26T00:00:00Z",
  };
  SHARES = [
    {
      conversation_id: CHAT,
      title: "the SOL grid",
      share_id: "s-1",
      revision: 1,
      shared_at: "2026-08-26T00:00:00Z",
      turn_count: 4,
    },
  ];
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("the chip stops claiming a withdrawn conversation is shared", () => {
  it("flips to Excluded when the share dialog unshares it", async () => {
    await render("dialog");
    expect(state()).toBe("shared");
    expect(chip()).toContain("Shared with Condor");

    await click("Unshare");

    expect(state()).toBe("excluded");
    expect(chip()).toContain("Excluded");
    // The offer, not just the wording: "Not this one" excludes a conversation
    // that is already excluded forever, which is the actionable half of the lie.
    expect(chip()).not.toContain("Not this one");
  });

  it("flips to Excluded when Settings → Privacy unshares the row", async () => {
    await render("settings");
    expect(state()).toBe("shared");

    await click("Unshare");

    expect(state()).toBe("excluded");
    expect(chip()).toContain("Excluded");
  });

  it("says so as soon as the dialog shares an unshared conversation", async () => {
    STATUS = { ...STATUS, shared: false, shared_at: null };
    await render("dialog");
    expect(state()).toBe("will-share");

    await click("Share");
    await click("Yes, send this");

    expect(state()).toBe("shared");
    expect(chip()).toContain("Shared with Condor");
  });
});

describe("the chip says what is redacted, not just that something was", () => {
  // READ-254. A comma-appended, subjectless `redacted` reads as a label stamped
  // on the conversation — a warning that something of the user's was cut —
  // rather than as the reassurance it is meant to be. Both states have to name
  // the noun (sensitive content) and the timing (first), and neither may claim
  // more than the scrubber delivers.
  it("names the noun and the timing once the share has gone out", async () => {
    await render("dialog");

    expect(state()).toBe("shared");
    expect(sentence()).toContain("Shared with Condor");
    expect(sentence()).toContain("Sensitive content was redacted first");
    // The withdrawal offer is the other half of the reassurance; a rewrite that
    // drops it trades one silence for another.
    expect(sentence()).toContain("Take it back any time");
    expect(sentence()).not.toMatch(/,\s*redacted\.?\s*$/);
  });

  it("reads as a sentence pair while the share is still pending", async () => {
    STATUS = { ...STATUS, shared: false, shared_at: null };
    await render("dialog");

    expect(state()).toBe("will-share");
    expect(sentence()).toContain("Will be shared with Condor when you are done");
    expect(sentence()).toContain("Sensitive content is redacted first");
    expect(sentence()).not.toMatch(/,\s*redacted\.?\s*$/);
  });
});
