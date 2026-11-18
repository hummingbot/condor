// jsdom doesn't implement every DOM API browsers do. An unguarded call to one
// of them (`scrollIntoView` is the one that has bitten us — CORR-1062, and
// before that the ChatThread fix) throws unhandled inside a React effect and
// exits the whole `vitest run` with 1 while every test still reports green.
// Stubbing the gap here, once, means a new call site doesn't need its own
// hand-rolled guard or per-file `Element.prototype.scrollIntoView = () => {}`
// to avoid reintroducing that flake.
if (typeof Element !== "undefined" && !Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function scrollIntoView() {};
}

// Same gap, a different API: `useTheme` reads `window.matchMedia` at module
// top level (`let currentTheme = getStoredTheme()`), so any test file whose
// module graph reaches it — anything importing `CodeEditor`, directly or
// through `StrategyConfigEditor`/the import review step (FEAT-202) — throws
// before a single test runs, with no stack trace pointing at the test's own
// code. Stubbed once here rather than per file, same reasoning as above.
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = ((media: string) => ({
    matches: false,
    media,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}
