/**
 * The openers on an empty chat: what this agent learned you ask it for, over
 * the static list its consumer ships (FEAT-073).
 *
 * Lives beside the component rather than inside it because `Starters.tsx`
 * exports a component and the lint gate lets it export nothing else — and
 * because both consumers, the workspace and the page bubble, need the same
 * merge rule and neither should own it.
 */
import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeftRight,
  Bot,
  ClipboardList,
  Droplets,
  Globe,
  LineChart,
  Search,
  Settings,
  ShieldAlert,
  Sparkles,
  Wallet,
  type LucideIcon,
} from "lucide-react";
import { useMemo } from "react";

import type { Starter } from "@/components/chat/Starters";
import { api, CHAT_SLUG, type StarterRow } from "@/lib/api";

/**
 * The glyphs a learned opener may ask for, by keyword.
 *
 * The vocabulary itself lives in `condor/agents/starters.py` — it is the
 * reflection prompt that has to be told what it may choose from. This is only
 * the half that turns a keyword into a picture, which is why an unknown one is
 * a `Sparkles` rather than an error: a chip whose icon this build has not
 * learned yet is still a perfectly good chip.
 */
const ICON_BY_KEYWORD: Record<string, LucideIcon> = {
  portfolio: Wallet,
  bot: Bot,
  risk: ShieldAlert,
  trade: ArrowLeftRight,
  chart: LineChart,
  lp: Droplets,
  market: Globe,
  report: ClipboardList,
  config: Settings,
  search: Search,
};

/** A learned row from the server, as the chip row renders it. */
export function starterFromRow(row: StarterRow): Starter {
  return {
    icon: ICON_BY_KEYWORD[row.icon] ?? Sparkles,
    title: row.title,
    hint: row.hint,
    prompt: row.prompt || row.title,
  };
}

/**
 * What to show: everything learned, then the defaults, capped at `limit`.
 *
 * A default whose title a learned row already covers is dropped rather than
 * shown twice. So nothing learned is today's behaviour byte for byte, one
 * thing learned sits above two defaults, and three things learned retire them
 * — no empty state to design and nothing to seed.
 */
export function mergeStarters(
  learned: Starter[],
  fallback: Starter[],
  limit = 3,
): Starter[] {
  const taken = new Set(learned.map((s) => s.title.trim().toLowerCase()));
  return [
    ...learned,
    ...fallback.filter((s) => !taken.has(s.title.trim().toLowerCase())),
  ].slice(0, limit);
}

/**
 * The openers to show for one agent: what it learned about you, then the
 * defaults underneath.
 *
 * Both consumers — the workspace and the page bubble — ask through here rather
 * than each running the query, so "learned rows outrank the static list" is one
 * rule in one place. A failed or still-loading fetch yields the fallback, which
 * is exactly the behaviour before any of this existed.
 */
export function useStarters(
  agentSlug: string,
  fallback: Starter[],
  enabled = true,
): Starter[] {
  const slug = agentSlug || CHAT_SLUG;
  const { data } = useQuery({
    queryKey: ["agent-starters", slug],
    queryFn: () => api.getAgentStarters(slug),
    enabled,
    // Reflection runs on a fifteen-minute tick, so there is nothing to gain
    // from re-asking on every focus of an empty chat.
    staleTime: 5 * 60 * 1000,
  });

  const learned = data?.starters;
  return useMemo(
    () => mergeStarters((learned ?? []).map(starterFromRow), fallback),
    [learned, fallback],
  );
}
