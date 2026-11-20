/**
 * The header's account menu (UX review, step 3): the one place for theme,
 * reporting an issue and signing out, each with a label rather than a bare
 * icon to decode.
 *
 * @vitest-environment jsdom
 */

import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const logout = vi.fn();
vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    user: { id: 1, username: "local", first_name: "Local", role: "admin" },
    logout,
  }),
}));
vi.mock("@/components/ui/AnchoredMenu", () => ({
  AnchoredMenu: ({ open, children }: { open: boolean; children: ReactNode }) =>
    open ? <div>{children}</div> : null,
}));

const { AccountMenu } = await import("./AccountMenu");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let host: HTMLDivElement;
let root: Root;
const onReportIssue = vi.fn();

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  logout.mockReset();
  onReportIssue.mockReset();
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => {
    root.render(
      <MemoryRouter>
        <AccountMenu onReportIssue={onReportIssue} />
      </MemoryRouter>,
    );
  });
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

const trigger = () => host.querySelector<HTMLButtonElement>('[aria-label="Account menu"]')!;
const button = (text: string) =>
  [...host.querySelectorAll("button")].find((b) => b.textContent?.trim() === text);
const open = () => act(() => trigger().click());

describe("AccountMenu", () => {
  it("shows the user's initial and stays closed until clicked", () => {
    expect(trigger().textContent).toBe("L");
    expect(button("Log out")).toBeUndefined();
    open();
    expect(host.textContent).toContain("Local");
    expect(button("Log out")).toBeDefined();
  });

  it("switches the theme from the menu", () => {
    open();
    act(() => button("Light")!.click());
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    act(() => button("Dark")!.click());
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });

  it("turns the colour-blind palette on and off", () => {
    open();
    const toggle = host.querySelector<HTMLInputElement>('input[role="switch"]')!;
    act(() => toggle.click());
    expect(document.documentElement.hasAttribute("data-colorblind")).toBe(true);
    act(() => toggle.click());
    expect(document.documentElement.hasAttribute("data-colorblind")).toBe(false);
  });

  it("opens the issue report and closes itself", () => {
    open();
    act(() => button("Report an issue")!.click());
    expect(onReportIssue).toHaveBeenCalledOnce();
    expect(button("Log out")).toBeUndefined();
  });

  it("logs out", () => {
    open();
    act(() => button("Log out")!.click());
    expect(logout).toHaveBeenCalledOnce();
  });
});
