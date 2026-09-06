import { useState } from "react";
import { api } from "../../api/client";
import { ApiError } from "../../types";

interface Props {
  workstreams: string[];
  onClose: () => void;
  /** Called after any change so the parent refetches workstreams AND tasks
   * (rename cascades onto tasks; delete reassigns them to "unassigned"). */
  onChanged: () => void;
}

export function WorkstreamModal({ workstreams, onClose, onChanged }: Props) {
  const [newName, setNewName] = useState("");
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Operation failed");
    } finally {
      setBusy(false);
    }
  };

  const add = () => {
    const name = newName.trim();
    if (!name) return;
    void run(async () => {
      await api.createWorkstream(name);
      setNewName("");
    });
  };

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal" onMouseDown={(e) => e.stopPropagation()}>
        <button className="modal-close" onClick={onClose} aria-label="Close">
          ×
        </button>
        <h2>🏷️ Manage workstreams</h2>
        {error && <div className="banner banner-error">{error}</div>}

        <div className="row">
          <input
            type="text"
            placeholder="New workstream name"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && add()}
          />
          <button className="btn btn-primary" onClick={add} disabled={busy}>
            Add
          </button>
        </div>

        {workstreams.length === 0 && <p className="muted">No workstreams yet.</p>}

        {workstreams.map((ws) => (
          <div className="row" key={ws}>
            <input
              type="text"
              value={edits[ws] ?? ws}
              onChange={(e) => setEdits((p) => ({ ...p, [ws]: e.target.value }))}
            />
            <button
              className="btn btn-sm"
              disabled={busy}
              onClick={() =>
                run(async () => {
                  const target = (edits[ws] ?? ws).trim();
                  if (target && target !== ws) await api.renameWorkstream(ws, target);
                })
              }
            >
              Rename
            </button>
            <button
              className="btn btn-sm btn-danger"
              disabled={busy}
              onClick={() =>
                run(async () => {
                  if (confirm(`Delete "${ws}"? Its tasks move to unassigned.`)) {
                    await api.deleteWorkstream(ws);
                  }
                })
              }
            >
              Delete
            </button>
          </div>
        ))}
        <p className="muted" style={{ fontSize: 12 }}>
          Renaming cascades onto tasks; deleting reassigns them to “unassigned”.
        </p>
      </div>
    </div>
  );
}
