import type { ReactNode } from "react";
import { useDroppable } from "@dnd-kit/core";
import { TASK_STATUS_LABELS, type TaskStatus } from "../../types";

interface Props {
  status: TaskStatus;
  count: number;
  children: ReactNode;
}

const STATUS_ICONS: Record<TaskStatus, string> = {
  todo: "📋",
  in_progress: "⚡",
  done: "✅",
};

/** A board column = one real status. Dropping a card here sets that status. */
export function Column({ status, count, children }: Props) {
  // Droppable id is namespaced so it never collides with a task (card) id.
  const { setNodeRef, isOver } = useDroppable({
    id: `col:${status}`,
    data: { status },
  });

  return (
    <div ref={setNodeRef} className={`column${isOver ? " drop-over" : ""}`}>
      <div className="column-title">
        <span>
          {STATUS_ICONS[status]} {TASK_STATUS_LABELS[status]}
        </span>
        <span>{count}</span>
      </div>
      {count === 0 ? <div className="column-empty">Drop here</div> : children}
    </div>
  );
}
