import { useState } from "react";
import { TASK_STATUSES, type Tag, type Task, type TaskStatus } from "../../types";
import { Column } from "./Column";
import { Card } from "./Card";

interface Props {
  label: string;
  tasks: Task[]; // already filtered, belonging to this lane
  tags: Tag[];
  defaultOpen: boolean;
  onStatusChange: (id: string, status: TaskStatus) => void;
  onDueChange: (id: string, due: string | null) => void;
  onTagsChange: (id: string, tags: string[]) => void;
  onDelete: (id: string) => void;
  onSend: (task: Task) => void;
  onCreateTag: (name: string) => Promise<void> | void;
}

/** One horizontal lane (a person or a workstream) with the 3 status columns. */
export function Swimlane({
  label,
  tasks,
  tags,
  defaultOpen,
  onStatusChange,
  onDueChange,
  onTagsChange,
  onDelete,
  onSend,
  onCreateTag,
}: Props) {
  const [open, setOpen] = useState(defaultOpen);
  const byStatus = (s: TaskStatus) => tasks.filter((t) => t.status === s);

  return (
    <section className="swimlane">
      <div className="swimlane-header" onClick={() => setOpen((v) => !v)}>
        <span>{open ? "▾" : "▸"}</span>
        <span>{label}</span>
        <span className="count">
          ({tasks.length} task{tasks.length === 1 ? "" : "s"})
        </span>
      </div>
      {open && (
        <div className="columns">
          {TASK_STATUSES.map((status) => {
            const colTasks = byStatus(status);
            return (
              <Column key={status} status={status} count={colTasks.length}>
                {colTasks.map((task) => (
                  <Card
                    key={task.id}
                    task={task}
                    tags={tags}
                    onStatusChange={(s) => onStatusChange(task.id, s)}
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
      )}
    </section>
  );
}
