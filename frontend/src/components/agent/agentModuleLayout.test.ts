/**
 * Each shared agent component lives in a file named for it (READ-407).
 *
 * `AgentOverviewTab.tsx` held `MarkdownEditor` (and two components since
 * deleted) long after its `OverviewTab` was removed, so a reader looking for
 * it had no file name to follow.
 */

import { describe, expect, it } from "vitest";

const agentModules = import.meta.glob("./*.tsx");

describe("agent component module layout (READ-407)", () => {
  it("has no AgentOverviewTab module", () => {
    expect(Object.keys(agentModules).filter((p) => p.includes("AgentOverviewTab"))).toEqual([]);
  });

  it.each(["MarkdownEditor"])("exports %s from %s.tsx", async (name) => {
    const load = agentModules[`./${name}.tsx`];
    expect(load).toBeDefined();
    const mod = (await load()) as Record<string, unknown>;
    expect(mod[name]).toBeDefined();
  });
});
