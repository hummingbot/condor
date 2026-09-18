import { useQuery } from "@tanstack/react-query";
import { useCallback, useMemo } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { AgentControls } from "@/components/agent/AgentControls";
import { effectiveTradingContext, parseRunId } from "@/components/agent/lab/runs";
import { AgentRunScreen } from "@/components/agent/workspace/AgentRunScreen";
import {
  lastPaneSection,
  openForPaneSection,
  rememberPaneSection,
  type PaneSection,
} from "@/components/agent/workspace/sections";
import {
  workspaceHref,
  type WorkspaceUrlAdapter,
  type WorkspaceUrlPatch,
} from "@/components/agent/workspace/workspaceUrl";
import type { PaneView } from "@/components/chat/paneUrl";
import { WorkspaceSheet } from "@/components/chat/WorkspaceSheet";
import { api } from "@/lib/api";

type StrategyPane = Extract<PaneView, { kind: "strategy" }>;

/**
 * Where a page reached from the pane goes back to — in `location.state`, so the
 * page's back control can put the reader in the conversation they came from,
 * with the pane on the section they were reading.
 */
export interface PaneReturnState {
  returnTo: string;
  section: PaneSection;
}

/**
 * A strategy, opened beside the conversation that made you want to open it.
 *
 * It is the agent's run screen — the same component `/agents/:slug` renders —
 * in its `pane` variant: the loop bar, then the answer stack and the four
 * sections as tabs (Now · Runs · Detail · Fleet · Playbook), one at a time. It used to be the strategy workbench, a different and smaller surface,
 * so expanding it to the page landed on a screen that shared almost nothing
 * with the one you had been reading, and collapsing back lost your place.
 *
 * Which tab, run and tick are open is in the chat's URL (`?sec=`, `?run=`,
 * `?tick=`, see `paneUrl`), so Back steps through them and the full-screen
 * door carries all three to the page — and the page's back control carries
 * them home again (see {@link PaneReturnState}).
 *
 * `paneProfile="tune"` — an even split, not a report's two thirds. This is a
 * surface you steer while reading what the agent says about it, and the chat
 * beside it is being used in the same minute.
 */
export function StrategySheet({
  pane,
  onPane,
  onClose,
}: {
  pane: StrategyPane;
  /** Move the pane — a tab, a run, a tick, or another of the agent's loops. */
  onPane: (next: StrategyPane) => void;
  /** Back to whatever held the pane before — in practice the agent panel. */
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const location = useLocation();
  const { agentSlug: slug, strategySlug: sslug } = pane;
  const section: PaneSection = pane.section ?? lastPaneSection();

  // The screen polls this key anyway; reading it here for the title is a
  // cache hit, and the fallback is the slug rather than a spinner in the bar.
  const { data: strategy } = useQuery({
    queryKey: ["strategy", slug, sslug],
    queryFn: () => api.getStrategy(slug, sslug),
    enabled: !!slug && !!sslug,
  });

  const onSection = useCallback(
    (next: PaneSection, patch?: WorkspaceUrlPatch) => {
      rememberPaneSection(next);
      onPane({
        ...pane,
        section: next,
        ...(patch && "run" in patch ? { run: patch.run ?? null, tick: null } : {}),
      });
    },
    [onPane, pane],
  );

  /**
   * The page's grammar, bound to the pane.
   *
   * The screen asks for `?strategy=/run=/tick=` moves; here the strategy is the
   * pane's loop and the other two are the pane's own keys. The page's cascades
   * hold: another loop drops the run and the tick, another run drops the tick.
   * A strategy of `null` is the Playbook's delete — the loop is gone, and so is
   * the sheet.
   */
  const adapter = useMemo<WorkspaceUrlAdapter>(() => {
    const url = {
      strategy: sslug,
      run: parseRunId(pane.run),
      tick: pane.tick ?? null,
      open: null,
    };
    const set = (patch: WorkspaceUrlPatch) => {
      if ("strategy" in patch && !patch.strategy) {
        onClose();
        return;
      }
      const moved = "strategy" in patch && patch.strategy !== sslug;
      const run =
        "run" in patch ? (patch.run ?? null) : moved ? null : (pane.run ?? null);
      const tick =
        "tick" in patch
          ? (patch.tick ?? null)
          : moved || "run" in patch
            ? null
            : (pane.tick ?? null);
      onPane({
        ...pane,
        strategySlug: moved ? patch.strategy! : sslug,
        section,
        run,
        tick,
      });
    };
    return { url, set };
  }, [onClose, onPane, pane, section, sslug]);

  // Full screen is the page, not a bigger sheet: the strategy has a URL, and a
  // reader who wants the whole window wants the thing they can link to. It
  // opens on the section the pane was on, and remembers the way back here.
  const expand = () => {
    const state: PaneReturnState = {
      returnTo: `${location.pathname}${location.search}`,
      section,
    };
    navigate(
      workspaceHref(slug, {
        strategy: sslug,
        run: pane.run ?? null,
        tick: pane.tick ?? null,
        open: openForPaneSection(section),
      }),
      { state },
    );
  };

  return (
    <WorkspaceSheet
      title={strategy?.name || sslug}
      subtitle={slug}
      paneProfile="tune"
      bleed
      onFullscreen={expand}
      onClose={onClose}
    >
      <AgentRunScreen
        slug={slug}
        adapter={adapter}
        variant="pane"
        section={section}
        onSection={onSection}
        header={({ strategy: s }) =>
          s ? (
            <div className="flex shrink-0 items-center justify-between gap-3 border-b border-[var(--color-border)] px-4 py-2">
              <p
                className="min-w-0 truncate text-xs text-[var(--color-text-muted)]"
                title={s.description}
              >
                {s.description}
              </p>
              <div className="shrink-0">
                <AgentControls
                  slug={slug}
                  sslug={s.slug}
                  status={s.status}
                  defaultContext={effectiveTradingContext(s)}
                  agentConfig={s.config}
                />
              </div>
            </div>
          ) : null
        }
      />
    </WorkspaceSheet>
  );
}
