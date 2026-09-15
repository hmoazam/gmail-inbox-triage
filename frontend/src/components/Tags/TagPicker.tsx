import { useEffect, useRef, useState } from "react";
import type { Tag } from "../../types";
import { fallbackColor } from "../../constants";

interface Props {
  selected: string[];
  tags: Tag[];
  /** Called with the new full tag list for the task. */
  onChange: (tags: string[]) => void;
  /** Register a new free-form tag so its color is stable (POST /api/tags). */
  onCreateTag?: (name: string) => Promise<void> | void;
}

/**
 * A "+ Tag" affordance opening a popover to toggle registry tags on/off and add
 * a new free-form tag. Adding/removing edits the task's tag list; a brand-new
 * name is registered in the tag store so its color stays consistent.
 */
export function TagPicker({ selected, tags, onChange, onCreateTag }: Props) {
  const [open, setOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const toggle = (name: string) => {
    if (selected.includes(name)) {
      onChange(selected.filter((t) => t !== name));
    } else {
      onChange([...selected, name]);
    }
  };

  const addNew = async () => {
    const name = newName.trim();
    if (!name) return;
    if (!selected.includes(name)) onChange([...selected, name]);
    if (!tags.some((t) => t.name === name) && onCreateTag) {
      await onCreateTag(name);
    }
    setNewName("");
  };

  return (
    <div className="tag-picker" ref={ref}>
      <button
        type="button"
        className="btn btn-sm"
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
      >
        + Tag
      </button>
      {open && (
        <div className="tag-picker-pop" onClick={(e) => e.stopPropagation()}>
          {tags.length === 0 && selected.length === 0 && (
            <div className="muted" style={{ fontSize: 12, padding: 4 }}>
              No tags yet — add one below.
            </div>
          )}
          {tags.map((t) => (
            <label key={t.name} className="opt">
              <input
                type="checkbox"
                checked={selected.includes(t.name)}
                onChange={() => toggle(t.name)}
              />
              <span className="swatch" style={{ background: t.color }} />
              {t.name}
            </label>
          ))}
          {/* Tags present on the task but not in the registry (edge case). */}
          {selected
            .filter((s) => !tags.some((t) => t.name === s))
            .map((s) => (
              <label key={s} className="opt">
                <input type="checkbox" checked onChange={() => toggle(s)} />
                <span className="swatch" style={{ background: fallbackColor(s) }} />
                {s}
              </label>
            ))}
          <div className="tag-picker-new">
            <input
              type="text"
              placeholder="New tag…"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void addNew();
                }
              }}
            />
            <button type="button" className="btn btn-sm" onClick={() => void addNew()}>
              Add
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
