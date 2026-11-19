import { Eye, Moon, Sun } from "lucide-react";

import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { useTheme, type Theme } from "@/hooks/useTheme";

/**
 * Settings → Appearance. Theme and the colour-blind palette are separate
 * choices: the palette is an accessibility overlay that works with either
 * theme, so it is a switch here rather than a third stop on the top-bar
 * theme toggle.
 */
export function AppearanceSettings() {
  const { theme, setTheme, colorblind, setColorblind } = useTheme();

  return (
    <div className="space-y-8">
      <section className="flex items-start justify-between gap-6">
        <div>
          <h3 className="text-sm font-semibold text-[var(--color-text)]">Theme</h3>
          <p className="mt-1 text-xs text-[var(--color-text-muted)]">
            Also switchable from the sun / moon button in the top bar.
          </p>
        </div>
        <SegmentedControl<Theme>
          aria-label="Theme"
          value={theme}
          onChange={setTheme}
          options={[
            { value: "light", label: "Light", icon: <Sun /> },
            { value: "dark", label: "Dark", icon: <Moon /> },
          ]}
        />
      </section>

      <section className="border-t border-[var(--color-border)] pt-8">
        <div className="flex items-start justify-between gap-6">
          <div>
            <h3 className="flex items-center gap-1.5 text-sm font-semibold text-[var(--color-text)]">
              <Eye className="h-4 w-4 text-[var(--color-text-muted)]" />
              Colour-blind mode
            </h3>
            <p className="mt-1 max-w-md text-xs leading-relaxed text-[var(--color-text-muted)]">
              Shows gains in blue and losses in orange instead of green and red, and uses blue for highlights.
              Safe for deuteranopia and protanopia. Works with both light and dark themes.
            </p>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={colorblind}
            aria-label="Colour-blind mode"
            onClick={() => setColorblind(!colorblind)}
            className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-primary)]/50 ${
              colorblind ? "bg-[var(--color-primary-fill)]" : "bg-[var(--color-border)]"
            }`}
          >
            <span
              className={`inline-block h-5 w-5 rounded-full bg-white shadow-sm transition-transform ${
                colorblind ? "translate-x-[22px]" : "translate-x-0.5"
              }`}
            />
          </button>
        </div>

        {/* Live preview of what the switch changes, in the current theme. */}
        <div className="mt-5 flex flex-wrap items-center gap-6 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] px-4 py-3">
          <span className="text-2xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">Preview</span>
          <span className="text-sm font-medium tabular-nums text-[var(--color-green)]">▲ +$12.40</span>
          <span className="text-sm font-medium tabular-nums text-[var(--color-red)]">▼ −$3.10</span>
          <span className="inline-flex h-7 items-center rounded-md bg-[var(--color-primary-fill)] px-3 text-xs font-medium text-[var(--on-primary)]">
            Primary action
          </span>
        </div>
      </section>
    </div>
  );
}
