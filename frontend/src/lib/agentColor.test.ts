import { describe, expect, it } from "vitest";

import { AGENT_COLOR_VARS, agentColor, agentColorVar, speakerNames } from "@/lib/agentColor";
import type { ChatMessage } from "@/hooks/useChatSocket";

function msg(partial: Partial<ChatMessage> & { role: ChatMessage["role"] }): ChatMessage {
  return { id: "m", text: "", toolCalls: [], ...partial };
}

describe("agentColorVar", () => {
  it("gives one slug the same colour every time", () => {
    const first = agentColorVar("backpack_mm");
    for (let i = 0; i < 10; i++) expect(agentColorVar("backpack_mm")).toBe(first);
  });

  it("only ever answers with the validated series palette", () => {
    const ids = ["", "condor", "brigado", "backpack_mm", "orca-lp", "Condor", "a", "zz"];
    for (const id of ids) expect(AGENT_COLOR_VARS).toContain(agentColorVar(id));
  });

  it("spreads short, prefix-sharing ids across the whole palette", () => {
    // Four colours cannot promise two given agents differ — the gutter is a
    // scanning aid, not an identifier. What it must not do is pile a realistic
    // roster onto one or two buckets, which is exactly what summing char codes
    // over ids this short and this similar does.
    const roster = [
      "Condor",
      "Brigado",
      "Backpack MM",
      "orca-lp",
      "hyperliquid-mm",
      "researcher",
      "backtester",
      "risk",
    ];
    expect(new Set(roster.map(agentColorVar)).size).toBe(AGENT_COLOR_VARS.length);
  });

  it("wraps into a custom property reference", () => {
    expect(agentColor("condor")).toBe(`var(${agentColorVar("condor")})`);
  });
});

describe("speakerNames", () => {
  it("credits the whole transcript to the bound agent when nobody handed over", () => {
    const messages = [
      msg({ role: "user", text: "hi" }),
      msg({ role: "assistant", text: "hello" }),
    ];
    expect(speakerNames(messages, "Condor")).toEqual(["Condor", "Condor"]);
  });

  it("switches speaker at the handover divider, not before it", () => {
    const messages = [
      msg({ role: "assistant", text: "before" }),
      msg({ role: "system", kind: "switch", text: "Switched to Brigado" }),
      msg({ role: "assistant", text: "after" }),
    ];
    expect(speakerNames(messages, "Condor")).toEqual(["Condor", "Brigado", "Brigado"]);
  });

  it("ignores a system line that is not a handover", () => {
    const messages = [
      msg({ role: "system", kind: "switch", text: "Now using server brigado" }),
      msg({ role: "assistant", text: "still me" }),
      msg({ role: "system", kind: "routine", text: "Switched to nothing at all" }),
      msg({ role: "assistant", text: "and still me" }),
    ];
    expect(speakerNames(messages, "Condor")).toEqual([
      "Condor",
      "Condor",
      "Condor",
      "Condor",
    ]);
  });
});
