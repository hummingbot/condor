import { Bug, LogOut, Moon, Settings as SettingsIcon, Sun } from "lucide-react";
import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";

import { AnchoredMenu } from "@/components/ui/AnchoredMenu";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { useTheme, type Theme } from "@/hooks/useTheme";
import { useAuth } from "@/lib/auth";

/**
 * The one personal menu in the top bar (UX review, roadmap step 3).
 *
 * Replaces two bare icons whose meaning had to be guessed: a bug that opened
 * "Report an issue" and a theme toggle. Everything about *you* — how the app
 * looks to you, reporting a problem, signing out — now sits behind your
 * initial, with words next to each item. The Advanced-mode switch is meant to
 * join it once that rollout lands (see the Default vs Advanced document).
 */
export function AccountMenu({ onReportIssue }: { onReportIssue: () => void }) {
  const { user, logout } = useAuth();
  const { theme, setTheme, colorblind, setColorblind } = useTheme();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const close = useCallback(() => setOpen(false), []);

  const name = user?.first_name || user?.username || "You";
  const initial = name.trim().charAt(0).toUpperCase() || "?";

  const item =
    "flex w-full items-center gap-2.5 px-3 py-1.5 text-left text-sm transition-colors hover:bg-[var(--color-surface-hover)]";

  return (
    <>
      <button
        ref={setAnchor}
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label="Account menu"
        title="Account"
        className="ml-1 flex h-7 w-7 items-center justify-center rounded-full border border-[var(--color-border)] bg-[var(--color-bg)] text-xs font-semibold text-[var(--color-text)] transition-colors hover:border-[var(--color-primary)]"
      >
        {initial}
      </button>

      <AnchoredMenu
        anchor={anchor}
        open={open}
        onClose={close}
        align="right"
        role="menu"
        className="w-60 py-1"
      >
        <div className="border-b border-[var(--color-border)] px-3 pb-2 pt-1.5">
          <div className="truncate text-sm font-medium">{name}</div>
          {user?.role && (
            <div className="text-[11px] capitalize text-[var(--color-text-muted)]">{user.role}</div>
          )}
        </div>

        <div className="space-y-2 border-b border-[var(--color-border)] px-3 py-2">
          <div className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
            Theme
          </div>
          <SegmentedControl<Theme>
            size="sm"
            fullWidth
            aria-label="Theme"
            value={theme}
            onChange={setTheme}
            options={[
              { value: "light", label: "Light", icon: <Sun /> },
              { value: "dark", label: "Dark", icon: <Moon /> },
            ]}
          />
          <label className="flex cursor-pointer items-center justify-between gap-2 text-sm">
            <span>Color-blind palette</span>
            <input
              type="checkbox"
              role="switch"
              checked={colorblind}
              onChange={(e) => setColorblind(e.target.checked)}
              className="h-4 w-4 accent-[var(--color-primary)]"
            />
          </label>
        </div>

        <div className="py-1">
          <button
            role="menuitem"
            className={item}
            onClick={() => {
              close();
              onReportIssue();
            }}
          >
            <Bug className="h-4 w-4 text-[var(--color-text-muted)]" />
            Report an issue
          </button>
          <button
            role="menuitem"
            className={item}
            onClick={() => {
              close();
              navigate("/settings");
            }}
          >
            <SettingsIcon className="h-4 w-4 text-[var(--color-text-muted)]" />
            Settings
          </button>
          <button
            role="menuitem"
            className={item}
            onClick={() => {
              close();
              logout();
            }}
          >
            <LogOut className="h-4 w-4 text-[var(--color-text-muted)]" />
            Log out
          </button>
        </div>
      </AnchoredMenu>
    </>
  );
}
