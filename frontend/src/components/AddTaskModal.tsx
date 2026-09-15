import { useState } from "react";
import {
  TASK_STATUSES,
  TASK_STATUS_LABELS,
  type TaskCreate,
  type TaskStatus,
} from "../types";
import { UNASSIGNED } from "../constants";

interface Props {
  assignees: string[];
  workstreams: string[];
  onClose: () => void;
  onCreate: (body: TaskCreate) => Promise<unknown>;
}

/** Manual "Add task" form (mirrors the Streamlit add-task expander). */
export function AddTaskModal({ assignees, workstreams, onClose, onCreate }: Props) {
  const [title, setTitle] = useState("");
  const [notes, setNotes] = useState("");
  const [assignee, setAssignee] = useState(assignees[0] ?? UNASSIGNED);
  const [workstream, setWorkstream] = useState(UNASSIGNED);
  const [dueDate, setDueDate] = useState("");
  const [status, setStatus] = useState<TaskStatus>("todo");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!title.trim()) {
      setError("Title is required.");
      return;
    }
    setBusy(true);
    setError(null);
    const created = await onCreate({
      title: title.trim(),
      context: notes.trim(),
      assignee,
      workstream,
      status,
      source: "manual",
      due_date: dueDate || null,
    });
    setBusy(false);
    if (created) onClose();
    else setError("Could not create task.");
  };

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal" onMouseDown={(e) => e.stopPropagation()}>
        <button className="modal-close" onClick={onClose} aria-label="Close">
          ×
        </button>
        <h2>➕ Add task</h2>
        {error && <div className="banner banner-error">{error}</div>}

        <div className="form-grid full">
          <label>
            Action / title *
            <input
              type="text"
              value={title}
              autoFocus
              onChange={(e) => setTitle(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void submit()}
            />
          </label>
          <label>
            Details / notes
            <textarea
              rows={4}
              value={notes}
              placeholder="Optional — add more detail or notes for this task"
              onChange={(e) => setNotes(e.target.value)}
            />
          </label>
        </div>

        <div className="form-grid">
          <label>
            Assignee
            <select value={assignee} onChange={(e) => setAssignee(e.target.value)}>
              {assignees.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
              <option value={UNASSIGNED}>{UNASSIGNED}</option>
            </select>
          </label>
          <label>
            Workstream
            <select value={workstream} onChange={(e) => setWorkstream(e.target.value)}>
              <option value={UNASSIGNED}>{UNASSIGNED}</option>
              {workstreams.map((w) => (
                <option key={w} value={w}>
                  {w}
                </option>
              ))}
            </select>
          </label>
          <label>
            Due date
            <input type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} />
          </label>
          <label>
            Status
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value as TaskStatus)}
            >
              {TASK_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {TASK_STATUS_LABELS[s]}
                </option>
              ))}
            </select>
          </label>
        </div>

        <button className="btn btn-primary" onClick={() => void submit()} disabled={busy}>
          Add task
        </button>
      </div>
    </div>
  );
}
