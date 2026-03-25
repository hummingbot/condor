/**
 * Minimal type declarations for TradingView Charting Library widget.
 * The full library is loaded from public/charting_library/ at runtime.
 */

interface TradingViewWidgetOptions {
  container: HTMLElement;
  locale?: string;
  library_path?: string;
  datafeed: unknown;
  symbol?: string;
  interval?: string;
  fullscreen?: boolean;
  autosize?: boolean;
  width?: number | string;
  height?: number | string;
  timezone?: string;
  theme?: "Light" | "Dark";
  debug?: boolean;
  disabled_features?: string[];
  enabled_features?: string[];
  overrides?: Record<string, string | number | boolean>;
  loading_screen?: { backgroundColor?: string; foregroundColor?: string };
  custom_css_url?: string;
  toolbar_bg?: string;
  auto_save_delay?: number;
  studies_overrides?: Record<string, string | number | boolean>;
}

interface TradingViewWidget {
  remove(): void;
  onChartReady(callback: () => void): void;
  activeChart(): {
    setSymbol(symbol: string, callback?: () => void): void;
    setResolution(resolution: string, callback?: () => void): void;
  };
}

interface TradingViewStatic {
  widget: new (options: TradingViewWidgetOptions) => TradingViewWidget;
}

interface Window {
  TradingView?: TradingViewStatic;
}
