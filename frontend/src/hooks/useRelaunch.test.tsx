/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { RelaunchResponse } from "@/lib/api";

const getRelaunch = vi.fn<() => Promise<RelaunchResponse>>();
vi.mock("@/lib/api", () => ({ api: { getRelaunch: () => getRelaunch() } }));

import { RELAUNCH_KEY, useReloadAfterRelaunch } from "./useRelaunch";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

function Harness() {
  const query = useReloadAfterRelaunch();
  useEffect(() => {
    if (query.isSuccess) observed.push(query.data.required);
  }, [query.data, query.isSuccess]);
  return null;
}

let observed: boolean[];
let container: HTMLDivElement;
let root: Root;
let client: QueryClient;
let reload: ReturnType<typeof vi.fn>;

async function mount() {
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <Harness />
      </QueryClientProvider>,
    );
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function poll() {
  await act(async () => {
    await client.invalidateQueries({ queryKey: RELAUNCH_KEY });
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  observed = [];
  getRelaunch.mockReset();
  reload = vi.fn();
  vi.stubGlobal(
    "window",
    Object.create(window, { location: { value: { reload } } }),
  );
  client = new QueryClient();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  client.clear();
  container.remove();
  vi.unstubAllGlobals();
});

describe("useReloadAfterRelaunch", () => {
  it("reloads exactly once after a successful required true to false transition", async () => {
    getRelaunch
      .mockResolvedValueOnce({ required: true })
      .mockResolvedValue({ required: false });

    await mount();
    expect(observed).toContain(true);
    expect(reload).not.toHaveBeenCalled();
    await poll();
    expect(getRelaunch).toHaveBeenCalledTimes(2);
    expect(client.getQueryData(RELAUNCH_KEY)).toEqual({ required: false });
    expect(reload).toHaveBeenCalledTimes(1);

    await poll();
    await poll();
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("never reloads a session that only sees required false", async () => {
    getRelaunch.mockResolvedValue({ required: false });

    await mount();
    await poll();
    expect(reload).not.toHaveBeenCalled();
  });

  it("does not mistake a failed poll for a completed relaunch", async () => {
    getRelaunch
      .mockResolvedValueOnce({ required: true })
      .mockRejectedValueOnce(new Error("server offline"))
      .mockResolvedValueOnce({ required: false });

    await mount();
    expect(observed).toContain(true);
    await act(async () => {
      void client.invalidateQueries({ queryKey: RELAUNCH_KEY });
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(getRelaunch).toHaveBeenCalledTimes(2);
    expect(reload).not.toHaveBeenCalled();

    await act(async () => {
      await client.refetchQueries({ queryKey: RELAUNCH_KEY });
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(getRelaunch).toHaveBeenCalledTimes(3);
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
