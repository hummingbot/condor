/**
 * Deep links survive the login, and unknown addresses render something (FU-13).
 *
 * @vitest-environment jsdom
 */

import { act, useEffect, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ProtectedRoute } from "./App";
import { AuthContext, type AuthState } from "./lib/auth";
import { Login } from "./pages/Login";
import { NotFound } from "./pages/NotFound";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;
let location = "";
let signIn: () => void;

function Where() {
  const { pathname, search, hash } = useLocation();
  useEffect(() => {
    location = pathname + search + hash;
  });
  return null;
}

function Harness({ start }: { start: string }) {
  const [isAuthenticated, setAuthenticated] = useState(false);
  useEffect(() => {
    signIn = () => setAuthenticated(true);
  }, []);
  const auth: AuthState = {
    user: null,
    token: null,
    isAuthenticated,
    loginWithToken: async () => {},
    loginLocal: async () => {},
    logout: () => {},
  };
  return (
    <AuthContext value={auth}>
      <MemoryRouter initialEntries={[start]}>
        <Where />
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route
            path="*"
            element={
              <ProtectedRoute>
                <div data-testid="page" />
              </ProtectedRoute>
            }
          />
        </Routes>
      </MemoryRouter>
    </AuthContext>
  );
}

async function mount(start: string) {
  await act(async () => {
    root.render(<Harness start={start} />);
  });
  await act(async () => {
    for (let i = 0; i < 3; i++) await new Promise((r) => setTimeout(r, 0));
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  // The login page asks how this install signs in; a Telegram install waits.
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ mode: "telegram" }))),
  );
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

describe("ProtectedRoute", () => {
  it("carries the address to the login and returns to it once signed in", async () => {
    await mount("/bots?group=type");
    expect(location).toBe("/login?redirect=%2Fbots%3Fgroup%3Dtype");
    expect(container.querySelector("[data-testid=page]")).toBeNull();

    await act(async () => signIn());
    expect(location).toBe("/bots?group=type");
    expect(container.querySelector("[data-testid=page]")).not.toBeNull();
  });

  it("adds no redirect for the home", async () => {
    await mount("/");
    expect(location).toBe("/login");
  });
});

describe("NotFound", () => {
  it("names the address and offers a way out", async () => {
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={["/nope"]}>
          <NotFound />
        </MemoryRouter>,
      );
    });
    expect(container.textContent).toContain("Page Not Found");
    expect(container.textContent).toContain("/nope");
    expect(container.querySelector("a")?.getAttribute("href")).toBe("/");
  });
});
