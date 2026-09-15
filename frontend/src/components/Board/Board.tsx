import {
  DndContext,
  PointerSensor,
  pointerWithin,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  PLANNED_DAYS,
  PLANNED_DAY_LABELS,
  type PlannedDay,
  type Tag,
  type Task,
  type TaskStatus,
} from "../../types";
import { Column, type ColumnKey } from "./Column";
import { Card } from "./Card";

interface Props {
  tasks: Task[]; // already filtered
  tags: Tag[];
  onStatusChange: (id: string, status: TaskStatus) => void;
  onTitleChange: (id: string, title: string) => void;
  onContextChange: (id: string, context: string) => void;
  onDayChange: (id: string, plannedDay: PlannedDay | null) => void;
  onDueChange: (id: string, due: string | null) => void;
  onTagsChange: (id: string, tags: string[]) => void;
  onDelete: (id: string) => void;
  onSend: (task: Task) => void;
  onCreateTag: (name: string) => Promise<void> | void;
}

// Column order: the five weekdays, then Backlog for unplanned tasks.
const COLUMNS: { key: ColumnKey; label: string }[] = [
  ...PLANNED_DAYS.map((d) => ({ key: d as ColumnKey, label: PLANNED_DAY_LABELS[d] })),
  { key: "backlog", label: "Backlog" },
];

export function Board({
  tasks,
  tags,
  onStatusChange,
  onTitleChange,
  onContextChange,
  onDayChange,
  onDueChange,
  onTagsChange,
  onDelete,
  onSend,
  onCreateTag,
}: Props) {
  // Require a small drag distance so a click on the handle doesn't misfire.
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }));

  const tasksForColumn = (key: ColumnKey): Task[] =>
    key === "backlog"
      ? tasks.filter((t) => t.planned_day == null)
      : tasks.filter((t) => t.planned_day === key);

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over) return;
    const targetKey = over.data.current?.columnKey as ColumnKey | undefined;
    if (!targetKey) return;
    const sourceDay = (active.data.current?.plannedDay ?? null) as PlannedDay | null;
    const targetDay: PlannedDay | null = targetKey === "backlog" ? null : targetKey;
    if (targetDay !== sourceDay) {
      onDayChange(String(active.id), targetDay);
    }
  };

  return (
    <DndContext sensors={sensors} collisionDetection={pointerWithin} onDragEnd={handleDragEnd}>
      <div className="board">
        {tasks.length === 0 && (
          <div className="empty-state">No tasks match the current filters.</div>
        )}
        <div className="day-columns">
          {COLUMNS.map((col) => {
            const colTasks = tasksForColumn(col.key);
            return (
              <Column key={col.key} columnKey={col.key} label={col.label} count={colTasks.length}>
                {colTasks.map((task) => (
                  <Card
                    key={task.id}
                    task={task}
                    tags={tags}
                    onStatusChange={(s) => onStatusChange(task.id, s)}
                    onTitleChange={(t) => onTitleChange(task.id, t)}
                    onContextChange={(c) => onContextChange(task.id, c)}
                    onDueChange={(d) => onDueChange(task.id, d)}
                    onTagsChange={(t) => onTagsChange(task.id, t)}
                    onDelete={() => onDelete(task.id)}
                    onSend={() => onSend(task)}
                    onCreateTag={onCreateTag}
                  />
                ))}
              </Column>
            );
          })}
        </div>
      </div>
    </DndContext>
  );
}
