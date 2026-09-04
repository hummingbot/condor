import { describe, expect, it } from "vitest";

import {
  bubbleAgentSlug,
  isAgentPage,
  normalizeAgentSlug,
} from "@/lib/agentSlug";
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

describe("isAgentPage", () => {
  it("is true for an agent's own page, Condor's included", () => {
    expect(isAgentPage("/agents/orca-lp")).toBe(true);
    expect(isAgentPage("/agents/orca-lp/knowledge")).toBe(true);
    // The case `bubbleAgentSlug` alone cannot answer: it normalizes to "",
    // exactly like /bots does.
    expect(isAgentPage("/agents/condor")).toBe(true);
    expect(bubbleAgentSlug("/agents/condor")).toBe(bubbleAgentSlug("/bots"));
  });

  it("is false for the directory and for every other route", () => {
    expect(isAgentPage("/agents")).toBe(false);
    expect(isAgentPage("/agents/")).toBe(false);
    expect(isAgentPage("/")).toBe(false);
    expect(isAgentPage("/bots")).toBe(false);
    expect(isAgentPage("/portfolio")).toBe(false);
  });
});
