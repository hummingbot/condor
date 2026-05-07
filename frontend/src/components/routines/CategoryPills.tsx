import { type RoutineInfo } from "@/lib/api";

interface CategoryPillsProps {
  routines: RoutineInfo[];
  activeCategory: string;
  onSelect: (category: string) => void;
}

export function CategoryPills({ routines, activeCategory, onSelect }: CategoryPillsProps) {
  const categories = new Map<string, number>();
  categories.set("All", routines.length);
  for (const r of routines) {
    const cat = r.category || "Uncategorized";
    categories.set(cat, (categories.get(cat) || 0) + 1);
  }

  return (
    <div className="flex gap-1.5 overflow-x-auto pb-1 scrollbar-none">
      {Array.from(categories.entries()).map(([cat, count]) => {
        const isActive = cat === activeCategory;
        return (
          <button
            key={cat}
            onClick={() => onSelect(cat)}
            className={`shrink-0 rounded-full px-3 py-1 text-xs font-medium transition-all ${
              isActive
                ? "bg-[var(--color-primary)] text-white shadow-sm"
                : "bg-[var(--color-surface-hover)] text-[var(--color-text-muted)] hover:bg-[var(--color-border)] hover:text-[var(--color-text)]"
            }`}
          >
            {cat}
            <span className={`ml-1.5 ${isActive ? "opacity-80" : "opacity-60"}`}>
              {count}
            </span>
          </button>
        );
      })}
    </div>
  );
}
