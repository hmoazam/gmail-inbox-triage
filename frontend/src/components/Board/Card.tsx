import { useState } from "react";
import { useDraggable } from "@dnd-kit/core";
import { CSS } from "@dnd-kit/utilities";
import {
  TASK_STATUS_LABELS,
  TASK_STATUSES,
  type Tag,
  type Task,
  type TaskStatus,
} from "../../types";
import { ME, SOURCE_ICONS } from "../../constants";
import { formatDueDate } from "../../utils";
import { TagChip } from "../Tags/TagChip";
import { TagPicker } from "../Tags/TagPicker";

interface Props {
  task: Task;
  tags: Tag[];
  onStatusChange: (status: TaskStatus) => void;
  onDueChange: (due: string | null) => void;
  onTagsChange: (tags: string[]) => void;
  onDelete: () => void;
  onSend: () => void;
  onCreateTag: (name: string) => Promise<void> | void;
}

export function Card({
  task,
  tags,
  onStatusChange,
  onDueChange,
  onTagsChange,
  onDelete,
  onSend,
  onCreateTag,
}: Props) {
  const [editingDue, setEditingDue] = useState(false);
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: task.id,
    // Drag between day columns moves planned_day; carry the source day so the
    // Board can skip a no-op PATCH when dropped back on the same column.
    data: { plannedDay: task.planned_day },
  });

  const style = transform ? { transform: CSS.Translate.toString(transform) } : undefined;
  const isTeammateTask = task.assignee !== ME && task.assignee !== "unassigned";
  const isDone = task.status === "done";

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`card${task.overdue ? " overdue" : ""}${isDragging ? " dragging" : ""}${
        isDone ? " done" : ""
      }`}
    >
      <div className="card-top">
        {/* Drag handle isolates DnD from the inline controls below. */}
        <span className="card-drag-handle" {...attributes} {...listeners} title="Drag to move">
          ⠿
        </span>
        <span title={task.source}>{SOURCE_ICONS[task.source]}</span>
        <span className="card-title">{task.title}</span>
      </div>

      {task.context && (
        <div className="thread-summary" title={task.context}>
          {task.context.length > 140 ? `${task.context.slice(0, 140)}…` : task.context}
        </div>
      )}

      <div className="card-meta">
        {task.due_date && (
          <span className={`due${task.overdue ? " overdue" : ""}`}>
            📅 {formatDueDate(task.due_date)}
            {task.overdue ? " — overdue" : ""}
          </span>
        )}
        {task.customer_related && <span className="badge">🧑‍💼 Customer</span>}
        <span className="badge" title="Owner">👤 {task.assignee}</span>
        <span className="badge" title="Workstream">🗂️ {task.workstream}</span>
      </div>

      <div className="tag-chips">
        {task.tags.map((name) => (
          <TagChip
            key={name}
            name={name}
            tags={tags}
            onRemove={() => onTagsChange(task.tags.filter((t) => t !== name))}
          />
        ))}
        <TagPicker
          selected={task.tags}
          tags={tags}
          onChange={onTagsChange}
          onCreateTag={onCreateTag}
        />
      </div>

      <div className="card-controls">
        <select
          value={task.status}
          onChange={(e) => onStatusChange(e.target.value as TaskStatus)}
          title="Status"
        >
          {TASK_STATUSES.map((s) => (
            <option key={s} value={s}>
              {TASK_STATUS_LABELS[s]}
            </option>
          ))}
        </select>

        {editingDue ? (
          <input
            type="date"
            value={task.due_date ?? ""}
            autoFocus
            onChange={(e) => {
              onDueChange(e.target.value || null);
              setEditingDue(false);
            }}
            onBlur={() => setEditingDue(false)}
          />
        ) : (
          <button type="button" className="btn btn-sm" onClick={() => setEditingDue(true)}>
            📅 {task.due_date ? "Edit" : "Set due"}
          </button>
        )}

        {isTeammateTask && (
          <button type="button" className="btn btn-sm" onClick={onSend} title="Draft an email">
            ✉️ Send
          </button>
        )}

        <button type="button" className="btn btn-sm btn-danger" onClick={onDelete} title="Delete">
          🗑️
        </button>

        {task.source_link && (
          <a className="card-link" href={task.source_link} target="_blank" rel="noreferrer">
            ↗ Source
          </a>
        )}
      </div>
    </div>
  );
}
