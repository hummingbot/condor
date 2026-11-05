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
