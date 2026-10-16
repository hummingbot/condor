/**
 * The bell's "Clear all" (PR #281).
 *
 * Clearing deletes the history for good, so what is pinned here is that it
 * only ever happens on a deliberate second click, that a failure says so, and
 * that the list shown afterwards is the store's and not a guess.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AppNotification, NotificationsResponse } from "@/lib/api";

const getNotifications = vi.fn();
const markNotificationsRead = vi.fn();
const clearNotifications = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getNotifications: (...a: unknown[]) => getNotifications(...a),
    markNotificationsRead: (...a: unknown[]) => markNotificationsRead(...a),
    clearNotifications: (...a: unknown[]) => clearNotifications(...a),
  },
}));

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ token: "t" }) }));
vi.mock("@/lib/appBadge", () => ({ setAppBadge: () => {} }));
vi.mock("@/lib/push", () => ({ onPushNavigate: () => () => {} }));
// The socket hook is a whole subsystem; the bell only needs its cache key.
vi.mock("@/hooks/useChatSocket", () => ({ NOTIFICATIONS_KEY: ["notifications"] }));
// Positioning is AnchoredMenu's own concern (and its own tests'); here it only
// has to show its children while open.
vi.mock("@/components/ui/AnchoredMenu", () => ({
  AnchoredMenu: ({ open, children }: { open: boolean; children: ReactNode }) =>
    open ? <div>{children}</div> : null,
}));

const { NotificationBell } = await import("./NotificationBell");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

function note(over: Partial<AppNotification> = {}): AppNotification {
  return {
    id: "n1",
    user_id: 1,
    ts: Date.now() / 1000,
    kind: "system",
    text: "Condor is online and ready.",
    title: null,
    link: null,
    read: true,
    ...over,
  } as AppNotification;
}

function listing(items: AppNotification[]): NotificationsResponse {
  return { items, unread: items.filter((n) => !n.read).length };
}

let container: HTMLDivElement;
let root: Root;

async function flush() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

function byLabel(label: string): HTMLButtonElement | null {
  return container.querySelector(`button[title="${label}"]`);
}

function byText(text: string): HTMLButtonElement | null {
  return (
    Array.from(container.querySelectorAll("button")).find(
      (b) => b.textContent?.trim() === text,
    ) ?? null
  );
}

async function click(el: Element | null) {
  expect(el).not.toBeNull();
  await act(async () => {
    (el as HTMLElement).click();
  });
  await flush();
}

async function render() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <NotificationBell />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  await flush();
}

const openBell = () => click(container.querySelector('button[aria-haspopup="menu"]'));

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  getNotifications.mockReset();
  markNotificationsRead.mockReset().mockResolvedValue({ unread: 0 });
  clearNotifications.mockReset();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("NotificationBell clear all", () => {
  it("deletes only on the second click, then re-reads the store", async () => {
    getNotifications.mockResolvedValue(listing([note()]));
    clearNotifications.mockResolvedValue({ unread: 0 });
    await render();
    await openBell();

    await click(byLabel("Clear all notifications"));
    expect(clearNotifications).not.toHaveBeenCalled();

    // The store has moved on by the time it is re-read: one arrived after the delete.
    getNotifications.mockResolvedValue(listing([note({ id: "n2", text: "Arrived after" })]));
    const readsBefore = getNotifications.mock.calls.length;

    await click(byText("Clear"));

    expect(clearNotifications).toHaveBeenCalledTimes(1);
    expect(getNotifications.mock.calls.length).toBeGreaterThan(readsBefore);
    expect(container.textContent).toContain("Arrived after");
    expect(container.textContent).not.toContain("Condor is online");
  });

  it("says so when the clear fails, and keeps Clear available to retry", async () => {
    getNotifications.mockResolvedValue(listing([note()]));
    clearNotifications.mockRejectedValue(new Error("500"));
    await render();
    await openBell();

    await click(byLabel("Clear all notifications"));
    await click(byText("Clear"));

    expect(container.querySelector('[role="alert"]')?.textContent).toContain(
      "Couldn't clear notifications",
    );
    expect(container.textContent).toContain("Condor is online");
    const retry = byText("Clear");
    expect(retry).not.toBeNull();
    expect(retry?.disabled).toBe(false);
  });

  it("disarms the confirm when a notification is opened", async () => {
    getNotifications.mockResolvedValue(listing([note({ link: "/bots" })]));
    await render();
    await openBell();

    await click(byLabel("Clear all notifications"));
    expect(byText("Clear")).not.toBeNull();

    const item = Array.from(container.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("Condor is online and ready."),
    );
    await click(item ?? null);
    await openBell();

    expect(byText("Clear")).toBeNull();
    expect(byLabel("Clear all notifications")).not.toBeNull();
    expect(clearNotifications).not.toHaveBeenCalled();
  });
});
