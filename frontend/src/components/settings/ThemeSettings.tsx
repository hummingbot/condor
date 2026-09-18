/**
 * How this browser looks: the palette, the currency amounts are quoted in, and
 * the face an address wears.
 *
 * All three were nowhere or half-hidden. The palette was a single icon in the
 * header that cycled dark → light → colour-blind, so finding a mode meant
 * clicking until it appeared and its name was only ever a tooltip. The currency
 * was a menu beside it. Both are preferences someone sets once, which is what
 * a settings page is for, and the header is for what changes as you work —
 * which server, which key.
 *
 * Per browser, not per account: this is how the screen in front of you is set
 * up, and two people sharing a machine each keep their own.
 */
import { Check, Eye, Moon, Sun, type LucideIcon } from "lucide-react";
import { useEffect, useState } from "react";

import {
  CURRENCY_OPTIONS,
  CURRENCY_SYMBOLS,
  useDisplayCurrency,
  type DisplayCurrency,
} from "@/hooks/useDisplayCurrency";
import { useTheme, type Theme } from "@/hooks/useTheme";
import { useWallet } from "@/lib/wallet/context";
import {
  AVATAR_STYLES,
  avatarSvg,
  loadAvatarStyle,
  setAvatarStyleId,
  useAvatarStyleId,
  useLoadAllAvatarStyles,
} from "@/lib/avatarStyle";

import { AddressAvatar } from "@/components/wallet/primitives";

const APPEARANCE: { id: Theme; label: string; hint: string; icon: LucideIcon }[] = [
  { id: "dark", label: "Dark", hint: "The default, and what most of these screens were drawn for.", icon: Moon },
  { id: "light", label: "Light", hint: "Same palette, inverted — for a bright room.", icon: Sun },
  {
    id: "colorblind",
    label: "Colour-blind",
    hint: "Up and down stop being red and green, which on a PnL chart is the whole signal.",
    icon: Eye,
  },
];

export function ThemeSettings() {
  const { theme, setTheme } = useTheme();
  const { currency, setCurrency } = useDisplayCurrency();
  const styleId = useAvatarStyleId();
  const loadAll = useLoadAllAvatarStyles();
  const { connected, attached } = useWallet();

  // The grid shows every style at once, and each is its own chunk — asked for
  // when this tab opens rather than by every page that draws one avatar.
  useEffect(() => loadAll(), [loadAll]);

  // Your own key if there is one, so the previews are the face you will
  // actually see rather than a stranger's.
  const seed = connected?.address ?? attached ?? "CondorVau1t1111111111111111111111111111111";

  return (
    <div className="space-y-8">
      <section>
        <h3 className="mb-1 text-sm font-semibold text-[var(--color-text)]">Appearance</h3>
        <p className="mb-3 text-xs text-[var(--color-text-muted)]">
          Applies to this browser, immediately.
        </p>
        <div className="grid gap-2 sm:grid-cols-3">
          {APPEARANCE.map((option) => {
            const Icon = option.icon;
            const active = theme === option.id;
            return (
              <button
                key={option.id}
                type="button"
                onClick={() => setTheme(option.id)}
                aria-pressed={active}
                className={`rounded-lg border p-3 text-left transition-colors ${
                  active
                    ? "border-[var(--color-primary)] bg-[var(--color-primary)]/5"
                    : "border-[var(--color-border)] bg-[var(--color-surface)] hover:border-[var(--color-border-hover)]"
                }`}
              >
                <span className="flex items-center gap-2">
                  <Icon className="h-4 w-4 text-[var(--color-text-muted)]" />
                  <span className="text-sm font-medium text-[var(--color-text)]">
                    {option.label}
                  </span>
                  {active && <Check className="ml-auto h-3.5 w-3.5 text-[var(--color-primary)]" />}
                </span>
                <span className="mt-1 block text-xs text-[var(--color-text-muted)]">
                  {option.hint}
                </span>
              </button>
            );
          })}
        </div>
      </section>

      <section>
        <h3 className="mb-1 text-sm font-semibold text-[var(--color-text)]">Display currency</h3>
        <p className="mb-3 text-xs text-[var(--color-text-muted)]">
          What balances and PnL are quoted in. Conversion is a display layer — nothing about an
          order or a position changes.
        </p>
        <div className="flex flex-wrap gap-2">
          {CURRENCY_OPTIONS.map((option: DisplayCurrency) => {
            const active = currency === option;
            return (
              <button
                key={option}
                type="button"
                onClick={() => setCurrency(option)}
                aria-pressed={active}
                className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm transition-colors ${
                  active
                    ? "border-[var(--color-primary)] bg-[var(--color-primary)]/5 text-[var(--color-primary)]"
                    : "border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text)] hover:border-[var(--color-border-hover)]"
                }`}
              >
                <span className="text-[var(--color-text-muted)]">{CURRENCY_SYMBOLS[option]}</span>
                {option}
              </button>
            );
          })}
        </div>
      </section>

      <section>
        <h3 className="mb-1 text-sm font-semibold text-[var(--color-text)]">Wallet avatar</h3>
        <p className="mb-3 text-xs text-[var(--color-text-muted)]">
          The generated face that identifies an address — wallets and vaults alike. Same address,
          same face, everywhere. Drawn in the page from the address itself, so it is never a
          request to anyone else&rsquo;s server.
        </p>
        <div className="mb-3 flex items-center gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
          <AddressAvatar address={seed} className="h-10 w-10" />
          <p className="font-mono text-xs text-[var(--color-text-muted)]">{seed}</p>
        </div>
        <div className="grid grid-cols-3 gap-3 sm:grid-cols-5">
          {AVATAR_STYLES.map((style) => {
            const active = styleId === style.id;
            return (
              <button
                key={style.id}
                type="button"
                onClick={() => setAvatarStyleId(style.id)}
                aria-pressed={active}
                className={`flex flex-col items-center gap-1.5 rounded-xl border p-3 transition-colors ${
                  active
                    ? "border-[var(--color-primary)] bg-[var(--color-primary)]/5"
                    : "border-[var(--color-border)] bg-[var(--color-surface)] hover:border-[var(--color-border-hover)]"
                }`}
              >
                <StylePreview seed={seed} styleId={style.id} />
                <span className="text-[11px] text-[var(--color-text-muted)]">{style.label}</span>
              </button>
            );
          })}
        </div>
      </section>
    </div>
  );
}

/** One preview, in a style that is not necessarily the chosen one — which is
 *  why this does not use `useAvatarSvg`, whose whole job is the chosen one. */
function StylePreview({ seed, styleId }: { seed: string; styleId: string }) {
  const [svg, setSvg] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    loadAvatarStyle(styleId)
      .then(() => {
        if (live) setSvg(avatarSvg(seed, styleId));
      })
      .catch(() => {
        // One style's chunk failing leaves that tile empty; the rest still
        // render, and the chosen style is loaded by its own path.
      });
    return () => {
      live = false;
    };
  }, [seed, styleId]);

  return (
    <span
      aria-hidden
      className="h-12 w-12 overflow-hidden rounded-full bg-[var(--color-surface-hover)] [&>svg]:h-full [&>svg]:w-full"
      dangerouslySetInnerHTML={svg ? { __html: svg } : undefined}
    />
  );
}
