import { Brain, FileText, FlaskConical, X, Zap } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import type { PanelId } from "./AgentToolbar";

const MIN_WIDTH = 360;
const MAX_WIDTH = 800;
const DEFAULT_WIDTH = 480;

const PANEL_META: Record<PanelId, { icon: typeof FileText; title: string }> = {
  strategy: { icon: FileText, title: "Strategy" },
  learnings: { icon: Brain, title: "Learnings" },
  routines: { icon: Zap, title: "Routines" },
  experiments: { icon: FlaskConical, title: "Dry-Run" },
};

interface AgentFloatingPanelProps {
  panelId: PanelId | null;
  onClose: () => void;
  children: React.ReactNode;
}

export function AgentFloatingPanel({ panelId, onClose, children }: AgentFloatingPanelProps) {
  const [width, setWidth] = useState(DEFAULT_WIDTH);
  const [isDragging, setIsDragging] = useState(false);
  const isOpen = panelId !== null;

  // Escape key closes
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [isOpen, onClose]);

  // Resize drag
  const startDrag = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      setIsDragging(true);
      const startX = e.clientX;
      const startWidth = width;
      const onMove = (ev: MouseEvent) => {
        const delta = startX - ev.clientX;
        setWidth(Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, startWidth + delta)));
      };
      const onUp = () => {
        setIsDragging(false);
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      };
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    },
    [width],
  );

  const meta = panelId ? PANEL_META[panelId] : null;
  const Icon = meta?.icon;

  return (
    <div
      style={{ width: isOpen ? width : 0 }}
      className={`fixed right-0 top-12 z-40 flex h-[calc(100%-3rem)] flex-col border-l border-[var(--color-border)] bg-[var(--color-bg)] shadow-xl transition-[width] duration-200 ease-out ${
        isOpen ? "" : "overflow-hidden border-l-0"
      }`}
    >
      {/* Resize handle */}
      {isOpen && (
        <div
          onMouseDown={startDrag}
          className={`group/resize absolute left-0 top-0 z-10 flex h-full w-1.5 cursor-col-resize items-center justify-center transition-colors hover:bg-[var(--color-primary)]/10 ${
            isDragging ? "bg-[var(--color-primary)]/20" : ""
          }`}
        >
          <div className="h-12 w-px rounded bg-amber-400/60 transition-colors group-hover/resize:bg-amber-400" />
        </div>
      )}

      {/* Header */}
      {isOpen && meta && Icon && (
        <div className="flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-2.5">
          <div className="flex items-center gap-2">
            <Icon className="h-4 w-4 text-[var(--color-primary)]" />
            <span className="text-sm font-semibold text-[var(--color-text)]">{meta.title}</span>
          </div>
          <button
            onClick={onClose}
            className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {/* Content */}
      {isOpen && (
        <div className="flex-1 overflow-y-auto p-4">
          {children}
        </div>
      )}
    </div>
  );
}
