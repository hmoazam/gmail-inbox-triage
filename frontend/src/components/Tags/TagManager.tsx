import { useState } from "react";
import { api } from "../../api/client";
import { ApiError, type Tag } from "../../types";
import { TAG_PALETTE } from "../../constants";

interface Props {
  tags: Tag[];
  onClose: () => void;
  /** Called after any change so the parent refetches tags AND tasks (a rename
   * may need tasks re-read if the backend cascades tag renames). */
  onChanged: () => void;
}

export function TagManager({ tags, onClose, onChanged }: Props) {
  const [newName, setNewName] = useState("");
  const [newColor, setNewColor] = useState(TAG_PALETTE[0]);
  const [edits, setEdits] = useState<Record<string, { name: string; color: string }>>({});
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
      await api.createTag({ name, color: newColor });
      setNewName("");
    });
  };

  const editFor = (t: Tag) => edits[t.name] ?? { name: t.name, color: t.color };

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal" onMouseDown={(e) => e.stopPropagation()}>
        <button className="modal-close" onClick={onClose} aria-label="Close">
          ×
        </button>
        <h2>🏷️ Manage tags</h2>
        {error && <div className="banner banner-error">{error}</div>}

        <div className="row">
          <input
            type="text"
            placeholder="New tag name"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && add()}
          />
          <input
            type="color"
            value={newColor}
            onChange={(e) => setNewColor(e.target.value)}
            title="Color"
          />
          <button className="btn btn-primary" onClick={add} disabled={busy}>
            Add
          </button>
        </div>

        {tags.length === 0 && <p className="muted">No tags yet.</p>}

        {tags.map((t) => {
          const e = editFor(t);
          return (
            <div className="row" key={t.name}>
              <input
                type="text"
                value={e.name}
                onChange={(ev) =>
                  setEdits((p) => ({ ...p, [t.name]: { ...e, name: ev.target.value } }))
                }
              />
              <input
                type="color"
                value={e.color}
                onChange={(ev) =>
                  setEdits((p) => ({ ...p, [t.name]: { ...e, color: ev.target.value } }))
                }
              />
              <button
                className="btn btn-sm"
                disabled={busy}
                onClick={() =>
                  run(async () => {
                    const patch: { name?: string; color?: string } = {};
                    if (e.name.trim() && e.name.trim() !== t.name) patch.name = e.name.trim();
                    if (e.color !== t.color) patch.color = e.color;
                    if (Object.keys(patch).length) await api.patchTag(t.name, patch);
                  })
                }
              >
                Save
              </button>
              <button
                className="btn btn-sm btn-danger"
                disabled={busy}
                onClick={() =>
                  run(async () => {
                    if (confirm(`Delete tag "${t.name}"?`)) await api.deleteTag(t.name);
                  })
                }
              >
                Delete
              </button>
            </div>
          );
        })}
        <p className="muted" style={{ fontSize: 12 }}>
          Colors keep chips consistent across cards. Deleting a tag removes it from the registry.
        </p>
      </div>
    </div>
  );
}
