import type { ReactNode } from "react";
import { useDroppable } from "@dnd-kit/core";
import type { PlannedDay } from "../../types";

/** A board column key: a weekday or the catch-all Backlog. */
export type ColumnKey = PlannedDay | "backlog";

interface Props {
  columnKey: ColumnKey;
  label: string;
  count: number;
  children: ReactNode;
}

const COLUMN_ICONS: Record<ColumnKey, string> = {
  mon: "🗓️",
  tue: "🗓️",
  wed: "🗓️",
  thu: "🗓️",
  fri: "🗓️",
  backlog: "📥",
};

/**
 * A board column = one weekday (or Backlog). Dropping a card here sets the
 * task's planned_day to this weekday, or null for Backlog.
 */
export function Column({ columnKey, label, count, children }: Props) {
  // Droppable id is namespaced so it never collides with a task (card) id.
  const { setNodeRef, isOver } = useDroppable({
    id: `col:${columnKey}`,
    data: { columnKey },
  });

  return (
    <div ref={setNodeRef} className={`column${isOver ? " drop-over" : ""}`}>
      <div className="column-title">
        <span>
          {COLUMN_ICONS[columnKey]} {label}
        </span>
        <span>{count}</span>
      </div>
      {count === 0 ? <div className="column-empty">Drop here</div> : children}
    </div>
  );
}
