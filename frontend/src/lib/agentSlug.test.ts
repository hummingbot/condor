import { describe, expect, it } from "vitest";

import { bubbleAgentSlug, normalizeAgentSlug } from "@/lib/agentSlug";
import { CHAT_SLUG } from "@/lib/api";

describe("normalizeAgentSlug", () => {
  it("maps the registry's name for Condor onto the chat's empty slug", () => {
    expect(normalizeAgentSlug(CHAT_SLUG)).toBe("");
    expect(normalizeAgentSlug("condor")).toBe("");
  });

  it("leaves a specialist's slug alone", () => {
    expect(normalizeAgentSlug("orca-lp")).toBe("orca-lp");
    // A slug that merely contains it is a different agent.
    expect(normalizeAgentSlug("condor-lite")).toBe("condor-lite");
  });

  it("treats nothing at all as Condor", () => {
    expect(normalizeAgentSlug("")).toBe("");
    expect(normalizeAgentSlug(null)).toBe("");
    expect(normalizeAgentSlug(undefined)).toBe("");
    expect(normalizeAgentSlug("  condor  ")).toBe("");
  });
});

describe("bubbleAgentSlug", () => {
  it("binds Condor's own page to the unbound chat", () => {
    expect(bubbleAgentSlug("/agents/condor")).toBe("");
    // ...and to the same one the bubble uses everywhere else, which is the
    // defect: two buckets meant two "Condor" conversations.
    expect(bubbleAgentSlug("/agents/condor")).toBe(bubbleAgentSlug("/portfolio"));
  });

  it("binds a specialist's page to that specialist", () => {
    expect(bubbleAgentSlug("/agents/orca-lp")).toBe("orca-lp");
    expect(bubbleAgentSlug("/agents/orca-lp/knowledge")).toBe("orca-lp");
    expect(bubbleAgentSlug("/agents/orca%20lp")).toBe("orca lp");
  });

  it("binds every other page to Condor", () => {
    expect(bubbleAgentSlug("/")).toBe("");
    expect(bubbleAgentSlug("/portfolio")).toBe("");
    expect(bubbleAgentSlug("/agents")).toBe("");
  });
});
