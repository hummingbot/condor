let current: { filename: string; title: string; source_name: string } | null = null;

export function setViewContext(ctx: typeof current) {
  current = ctx;
}

export function getViewContext() {
  return current;
}
