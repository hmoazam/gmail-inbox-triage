import { useState } from "react";
import { api } from "../../api/client";
import {
  ApiError,
  TRIAGE_CATEGORIES,
  TRIAGE_CATEGORY_LABELS,
  type TriageCategory,
  type TriageThread,
} from "../../types";

interface Props {
  /** Surface a Google-auth-expired message verbatim in the global banner. */
  onAuth: (message: string) => void;
  /** Called after a thread is added to the board so the board refetches. */
  onAddedToBoard: () => void;
}

const CATEGORY_META: Record<TriageCategory, { icon: string; sub: string }> = {
  action_required: { icon: "🚨", sub: "Threads with a concrete action on you." },
  useful: { icon: "💡", sub: "No action needed, but worth being aware of." },
  other: { icon: "🗂️", sub: "Everything else." },
};

export function Triage({ onAuth, onAddedToBoard }: Props) {
  const [threads, setThreads] = useState<TriageThread[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [added, setAdded] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("in:inbox is:unread");
  const [cap, setCap] = useState(false);
  const [maxResults, setMaxResults] = useState(30);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasFetched, setHasFetched] = useState(false);

  const handleErr = (err: unknown) => {
    if (err instanceof ApiError && err.isAuth) onAuth(err.message);
    else setError(err instanceof ApiError ? err.message : "Request failed");
  };

  const fetchThreads = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.fetchTriage({ query, maxResults: cap ? maxResults : undefined });
      setThreads(res.threads);
      setSelected(new Set());
      setHasFetched(true);
    } catch (err) {
      handleErr(err);
    } finally {
      setLoading(false);
    }
  };

  const toggleSel = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const toggleExpand = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const markSelectedRead = async () => {
    if (selected.size === 0) return;
    // mark-read takes Gmail MESSAGE ids — gather them from selected threads.
    const messageIds = threads
      .filter((t) => selected.has(t.thread_id))
      .flatMap((t) => t.messages.map((m) => m.id));
    if (messageIds.length === 0) return;
    try {
      await api.markRead({ message_ids: messageIds });
      // Drop the marked threads from view.
      setThreads((prev) => prev.filter((t) => !selected.has(t.thread_id)));
      setSelected(new Set());
    } catch (err) {
      handleErr(err);
    }
  };

  const addToBoard = async (t: TriageThread) => {
    const d = t.decision;
    try {
      // add-to-board carries the thread's decision fields; returns {created, task}.
      const res = await api.addToBoard({
        thread_id: t.thread_id,
        category: d.category,
        summary: d.summary,
        action_on_me: d.action_on_me,
        customer_related: d.customer_related,
        internal_only: d.internal_only,
        needs_response: d.needs_response,
        confidence: d.confidence,
      });
      // created:false means it was already on the board — still a success no-op.
      setAdded((prev) => new Set(prev).add(t.thread_id));
      if (res.created) onAddedToBoard();
    } catch (err) {
      handleErr(err);
    }
  };

  const byCategory = (c: TriageCategory) => threads.filter((t) => t.decision.category === c);

  return (
    <div className="triage">
      <div className="triage-toolbar">
        <label style={{ flex: 1, minWidth: 260 }}>
          Gmail search filter
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="in:inbox is:unread"
          />
        </label>
        <label className="checkbox" style={{ flexDirection: "row", alignItems: "center", gap: 5 }}>
          <input type="checkbox" checked={cap} onChange={(e) => setCap(e.target.checked)} />
          Cap results
        </label>
        {cap && (
          <label>
            Max threads
            <input
              type="number"
              min={5}
              max={200}
              value={maxResults}
              onChange={(e) => setMaxResults(Number(e.target.value))}
            />
          </label>
        )}
        <button className="btn btn-primary" onClick={() => void fetchThreads()} disabled={loading}>
          {loading ? "Classifying…" : "🔄 Fetch & classify"}
        </button>
        <button className="btn" onClick={() => void markSelectedRead()} disabled={selected.size === 0}>
          {selected.size > 0
            ? `✅ Mark ${selected.size} selected as read`
            : "Mark as read (select threads…)"}
        </button>
      </div>

      {error && <div className="banner banner-error">{error}</div>}

      {!hasFetched && !loading && (
        <div className="empty-state">
          Enter a Gmail filter and hit “Fetch &amp; classify” to triage your inbox.
        </div>
      )}

      {hasFetched &&
        TRIAGE_CATEGORIES.map((cat) => {
          const items = byCategory(cat);
          const meta = CATEGORY_META[cat];
          return (
            <div className="triage-bucket" key={cat}>
              <div className="triage-bucket-header">
                {meta.icon} {TRIAGE_CATEGORY_LABELS[cat]}{" "}
                <span className="muted" style={{ fontWeight: 400 }}>
                  ({items.length})
                </span>
              </div>
              <div className="triage-bucket-sub">{meta.sub}</div>
              {items.length === 0 && <div className="muted">Nothing here.</div>}
              {items.map((t) => (
                <div className="thread" key={t.thread_id}>
                  <input
                    type="checkbox"
                    checked={selected.has(t.thread_id)}
                    onChange={() => toggleSel(t.thread_id)}
                    aria-label="select thread"
                  />
                  <div className="thread-body">
                    <div className="thread-subject">
                      {t.unread ? "🔵 " : ""}
                      {t.subject || "(no subject)"}
                    </div>
                    <div className="muted" style={{ fontSize: 12 }}>
                      {t.participants.slice(0, 4).join(", ")}
                      {t.participants.length > 4 ? ` +${t.participants.length - 4}` : ""}
                      {t.message_count > 0
                        ? `${t.participants.length ? "  ·  " : ""}${t.message_count} msg${
                            t.message_count === 1 ? "" : "s"
                          }`
                        : ""}
                    </div>
                    {cat === "action_required" && t.decision.action_on_me && (
                      <div className="thread-action">🚨 Action: {t.decision.action_on_me}</div>
                    )}
                    {t.decision.summary && (
                      <div className="thread-summary">
                        💬 {t.decision.summary}
                        {t.decision.confidence
                          ? `  ·  ${Math.round(t.decision.confidence * 100)}%`
                          : ""}
                      </div>
                    )}
                    <div className="card-meta">
                      {t.decision.customer_related && <span className="badge">🧑‍💼 Customer</span>}
                      {t.decision.internal_only && <span className="badge">🏢 Internal</span>}
                      {t.decision.needs_response && <span className="badge">↩️ Needs response</span>}
                      {t.source_link && (
                        <a
                          className="card-link"
                          href={t.source_link}
                          target="_blank"
                          rel="noreferrer"
                        >
                          ↗ Open in Gmail
                        </a>
                      )}
                    </div>
                    {cat === "action_required" && (
                      <div>
                        <button
                          className="btn btn-sm btn-primary"
                          disabled={added.has(t.thread_id)}
                          onClick={() => void addToBoard(t)}
                        >
                          {added.has(t.thread_id) ? "✓ Added" : "📋 Add to board"}
                        </button>
                      </div>
                    )}
                    <button
                      className="btn btn-sm"
                      style={{ alignSelf: "flex-start" }}
                      onClick={() => toggleExpand(t.thread_id)}
                    >
                      {expanded.has(t.thread_id) ? "Hide" : "Read thread"}
                    </button>
                    {expanded.has(t.thread_id) &&
                      (() => {
                        // Derive last-message from/date from messages[].
                        const last = t.messages[t.messages.length - 1];
                        return (
                          <div className="thread-preview">
                            {last?.from ? `From: ${last.from}\n` : ""}
                            {last?.date ? `Date: ${last.date}\n\n` : ""}
                            {t.decision.summary || "No preview available."}
                          </div>
                        );
                      })()}
                  </div>
                </div>
              ))}
            </div>
          );
        })}
    </div>
  );
}
