import { useState } from "react";
import { api } from "../../api/client";
import { ApiError, type SlackAction, type Tag } from "../../types";
import { ME, UNASSIGNED } from "../../constants";
import { TagChip } from "../Tags/TagChip";
import { TagPicker } from "../Tags/TagPicker";

interface Props {
  /** Surface a dbexec/Slack-unavailable message verbatim in the global banner. */
  onAuth: (message: string) => void;
  /** Called after an action is added to the board so the board refetches. */
  onAddedToBoard: () => void;
  /** Workstream options for the per-action picker (reused from the board). */
  workstreams: string[];
  /** Tag registry for the per-action tag picker. */
  tags: Tag[];
  /** Register a brand-new free-form tag so its color is stable (POST /api/tags). */
  onCreateTag: (name: string) => Promise<void> | void;
}

export function SlackTriage({ onAuth, onAddedToBoard, workstreams, tags, onCreateTag }: Props) {
  const [url, setUrl] = useState("");
  const [sourceLink, setSourceLink] = useState<string>("");
  const [actions, setActions] = useState<SlackAction[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleErr = (err: unknown) => {
    if (err instanceof ApiError && err.isAuth) onAuth(err.message);
    else setError(err instanceof ApiError ? err.message : "Request failed");
  };

  const extract = async () => {
    if (!url.trim() || loading) return;
    setLoading(true);
    setError(null);
    setActions(null);
    try {
      const res = await api.extractSlackActions(url.trim());
      setSourceLink(res.source_link);
      setActions(res.actions);
    } catch (err) {
      handleErr(err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="triage">
      <div className="triage-toolbar">
        <label style={{ flex: 1, minWidth: 320 }}>
          Paste a Slack channel or thread URL
          <input
            type="text"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://…slack.com/archives/C…/p…"
            onKeyDown={(e) => e.key === "Enter" && void extract()}
            disabled={loading}
          />
        </label>
        <button
          className="btn btn-primary"
          onClick={() => void extract()}
          disabled={loading || !url.trim()}
        >
          {loading ? "Extracting…" : "🔍 Extract my actions"}
        </button>
      </div>

      {error && <div className="banner banner-error">{error}</div>}

      {loading && (
        <div className="empty-state">
          ⏳ Reading the conversation and extracting your actions… this can take up to
          ~2&nbsp;minutes. Please keep this tab open.
        </div>
      )}

      {!loading && actions === null && !error && (
        <div className="empty-state">
          Paste a Slack channel or thread URL and hit “Extract my actions” to pull the tasks you
          still need to do.
        </div>
      )}

      {!loading && actions !== null && actions.length === 0 && (
        <div className="empty-state">No pending actions found in that conversation.</div>
      )}

      {!loading &&
        actions !== null &&
        actions.length > 0 &&
        actions.map((action, i) => (
          <ActionRow
            key={i}
            action={action}
            sourceLink={sourceLink}
            workstreams={workstreams}
            tags={tags}
            onCreateTag={onCreateTag}
            onAdded={onAddedToBoard}
            onError={handleErr}
          />
        ))}
    </div>
  );
}

interface RowProps {
  action: SlackAction;
  sourceLink: string;
  workstreams: string[];
  tags: Tag[];
  onCreateTag: (name: string) => Promise<void> | void;
  onAdded: () => void;
  onError: (err: unknown) => void;
}

/** One editable proposed task extracted from the conversation. */
function ActionRow({
  action,
  sourceLink,
  workstreams,
  tags,
  onCreateTag,
  onAdded,
  onError,
}: RowProps) {
  const [title, setTitle] = useState(action.task);
  const [context, setContext] = useState(action.context);
  const [due, setDue] = useState(action.due ?? "");
  const [workstream, setWorkstream] = useState(UNASSIGNED);
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [added, setAdded] = useState(false);

  const addToBoard = async () => {
    if (!title.trim() || busy || added) return;
    setBusy(true);
    try {
      await api.createTask({
        title: title.trim(),
        context,
        due_date: due || null,
        source: "slack",
        source_link: sourceLink,
        assignee: ME,
        workstream,
        tags: selectedTags,
      });
      setAdded(true);
      onAdded();
    } catch (err) {
      onError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="thread">
      <div className="thread-body">
        <label style={{ fontSize: 12, color: "var(--text-muted)", fontWeight: 600 }}>
          Action / title
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            style={{ fontWeight: 600, fontSize: 14 }}
          />
        </label>

        <label style={{ fontSize: 12, color: "var(--text-muted)", fontWeight: 600 }}>
          Context
          <textarea
            value={context}
            onChange={(e) => setContext(e.target.value)}
            rows={2}
            style={{ fontFamily: "inherit", fontSize: 13, resize: "vertical" }}
          />
        </label>

        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>
          <label style={{ fontSize: 12, color: "var(--text-muted)", fontWeight: 600 }}>
            Due date
            <input type="date" value={due} onChange={(e) => setDue(e.target.value)} />
          </label>
          <label style={{ fontSize: 12, color: "var(--text-muted)", fontWeight: 600 }}>
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
        </div>

        <div className="tag-chips">
          {selectedTags.map((name) => (
            <TagChip
              key={name}
              name={name}
              tags={tags}
              onRemove={() => setSelectedTags((prev) => prev.filter((t) => t !== name))}
            />
          ))}
          <TagPicker
            selected={selectedTags}
            tags={tags}
            onChange={setSelectedTags}
            onCreateTag={onCreateTag}
          />
        </div>

        <div className="card-meta">
          {sourceLink && (
            <a className="card-link" href={sourceLink} target="_blank" rel="noreferrer">
              ↗ Open in Slack
            </a>
          )}
        </div>

        <div>
          <button
            className="btn btn-sm btn-primary"
            disabled={added || busy || !title.trim()}
            onClick={() => void addToBoard()}
          >
            {added ? "✓ Added" : busy ? "Adding…" : "📋 Add to board"}
          </button>
        </div>
      </div>
    </div>
  );
}
