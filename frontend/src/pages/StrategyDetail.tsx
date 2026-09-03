import { ArrowLeft } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";

import { StrategyWorkbench } from "@/components/agent/StrategyWorkbench";

// ── Strategy Detail Page ──
//
// A strategy is a playbook that loops under an Agent. Everything you can read
// or do to one lives in `StrategyWorkbench`, which this page and the chat
// workspace's `StrategySheet` both host — so a strategy opened beside a
// conversation is the same strategy, not a preview of it.
//
// What is left here is what only a page has: the URL, and the way back up to
// the owning Agent. The identity above it lives at /agents/:slug.

export function StrategyDetail() {
  const { slug, sslug } = useParams<{ slug: string; sslug: string }>();
  const navigate = useNavigate();

  return (
    <div className="w-full">
      <button
        onClick={() => navigate(`/agents/${slug}`)}
        className="mb-3 flex items-center gap-1 text-xs text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
      >
        <ArrowLeft className="h-3.5 w-3.5" /> Back to Agent
      </button>
      <StrategyWorkbench
        slug={slug!}
        sslug={sslug!}
        onDeleted={() => navigate(`/agents/${slug}`)}
      />
    </div>
  );
}
