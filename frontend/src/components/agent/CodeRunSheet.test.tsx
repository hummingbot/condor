/**
 * The code-run sheet reads a run's measured duration with the same rule as the
 * feed row it opens from (ARCH-404), so 1,500 ms is `1.5s` in both.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CodeRun, DelegationSummary } from "@/lib/api";
import { CodeRunSheet } from "./CodeRunSheet";

let RUN: Partial<CodeRun> = {};

vi.mock("@/lib/api", () => ({
  api: { getCodeRun: vi.fn(async () => RUN) },
}));

// The sheet's chrome (portal, pane sizing) is not what this pins.
vi.mock("@/components/chat/WorkspaceSheet", () => ({
  WorkspaceSheet: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const ROW: DelegationSummary = {
  task_id: "c",
  agent: "scout",
  user_id: 7,
  chat_id: 42,
  server_name: null,
  task: "returns of SOL 1h",
  status: "done",
  kind: "code",
  caller: "",
  conversation_id: "",
  started_at: 1_000,
  ended_at: 1_001.5,
};

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("CodeRunSheet", () => {
  it("reads 1,500 ms as 1.5s, the same as the feed row", async () => {
    RUN = {
      id: "c",
      status: "ok",
      duration_ms: 1500,
      server: "",
      code: "print(1)",
      stdout: "1",
      result: "",
      error: "",
      traceback: "",
    };
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <CodeRunSheet row={ROW} onClose={() => {}} />
        </QueryClientProvider>,
      );
    });
    for (let i = 0; i < 5; i++) {
      await act(async () => {
        await new Promise((r) => setTimeout(r, 0));
      });
    }

    expect(container.querySelector("[data-code-duration]")!.textContent).toBe("1.5s");
  });
});
