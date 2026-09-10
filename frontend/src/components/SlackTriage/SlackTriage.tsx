import { useState } from "react";
import { api } from "../../api/client";
import {
  ApiError,
  TRIAGE_CATEGORIES,
  TRIAGE_CATEGORY_LABELS,
  type SlackConversation,
  type SlackGroup,
  type TriageCategory,
} from "../../types";

interface Props {
  /** Surface a Slack-auth-expired / missing-scope message in the global banner. */
  onAuth: (message: string) => void;
  /** Called after a conversation is added to the board so the board refetches. */
  onAddedToBoard: () => void;
}

const CATEGORY_META: Record<TriageCategory, { icon: string; sub: string }> = {
  action_required: { icon: "🚨", sub: "Conversations with a concrete action on you." },
  useful: { icon: "💡", sub: "No action needed, but worth being aware of." },
  other: { icon: "🗂️", sub: "Everything else." },
};

export function SlackTriage({ onAuth, onAddedToBoard }: Props) {
  const [groups, setGroups] = useState<SlackGroup[]>([]);
  const [added, setAdded] = useState<Set<string>>(new Set());
  const [marked, setMarked] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasFetched, setHasFetched] = useState(false);

  const handleErr = (err: unknown) => {
    if (err instanceof ApiError && err.isAuth) onAuth(err.message);
    else setError(err instanceof ApiError ? err.message : "Request failed");
  };

  const fetchGroups = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.fetchSlackTriage();
      setGroups(res.groups);
      setAdded(new Set());
      setMarked(new Set());
      setHasFetched(true);
    } catch (err) {
      handleErr(err);
    } finally {
      setLoading(false);
    }
  };

  const toggleExpand = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const markRead = async (c: SlackConversation) => {
    try {
      // mark-read takes the channel id + the latest unread ts.
      await api.slackMarkRead({ channel_id: c.id, ts: c.latest_ts });
      // Drop the conversation from every group it appears in.
      setGroups((prev) =>
        prev.map((g) => ({
          ...g,
          conversations: g.conversations.filter((x) => x.id !== c.id),
        })),
      );
      setMarked((prev) => new Set(prev).add(c.id));
    } catch (err) {
      handleErr(err);
    }
  };

  const addToBoard = async (c: SlackConversation) => {
    const d = c.decision;
    try {
      // add-to-board carries the conversation's decision fields; returns
      // {created, task}. created:false means it was already on the board.
      const res = await api.slackAddToBoard({
        channel_id: c.id,
        name: c.name,
        permalink: c.permalink,
        summary: d.summary,
        action_on_me: d.action_on_me,
        customer_related: d.customer_related,
        needs_response: d.needs_response,
        confidence: d.confidence,
      });
      setAdded((prev) => new Set(prev).add(c.id));
      if (res.created) onAddedToBoard();
    } catch (err) {
      handleErr(err);
    }
  };

  const groupCount = (g: SlackGroup) => g.conversations.length;

  return (
    <div className="triage">
      <div className="triage-toolbar">
        <div style={{ flex: 1, minWidth: 260, fontSize: 13, color: "var(--text-muted)" }}>
          Unread Slack DMs and channel messages, grouped by category and classified with Claude.
        </div>
        <button className="btn btn-primary" onClick={() => void fetchGroups()} disabled={loading}>
          {loading ? "Classifying…" : "🔄 Fetch & classify"}
        </button>
      </div>

      {error && <div className="banner banner-error">{error}</div>}

      {!hasFetched && !loading && (
        <div className="empty-state">
          Hit “Fetch &amp; classify” to triage your unread Slack messages.
        </div>
      )}

      {hasFetched && groups.length === 0 && (
        <div className="empty-state">No unread Slack conversations. 🎉</div>
      )}

      {hasFetched &&
        groups.map((group) => (
          <div className="slack-group" key={group.category}>
            <div className="slack-group-header">
              💬 {group.category}{" "}
              <span className="muted" style={{ fontWeight: 400 }}>
                ({groupCount(group)})
              </span>
            </div>

            {groupCount(group) === 0 && <div className="muted">Nothing unread here.</div>}

            {groupCount(group) > 0 &&
              TRIAGE_CATEGORIES.map((cat) => {
                const items = group.conversations.filter((c) => c.decision.category === cat);
                if (items.length === 0) return null;
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
                    {items.map((c) => (
                      <div className="thread" key={c.id}>
                        <div className="thread-body">
                          <div className="thread-subject">
                            {c.kind === "im" ? "👤 " : "#"}
                            {c.name}
                            {c.unread_count > 0 ? (
                              <span className="muted" style={{ fontWeight: 400, fontSize: 12 }}>
                                {"  ·  "}
                                {c.unread_count} unread
                              </span>
                            ) : (
                              ""
                            )}
                          </div>
                          {cat === "action_required" && c.decision.action_on_me && (
                            <div className="thread-action">🚨 Action: {c.decision.action_on_me}</div>
                          )}
                          {c.decision.summary && (
                            <div className="thread-summary">
                              💬 {c.decision.summary}
                              {c.decision.confidence
                                ? `  ·  ${Math.round(c.decision.confidence * 100)}%`
                                : ""}
                            </div>
                          )}
                          <div className="card-meta">
                            {c.decision.customer_related && (
                              <span className="badge">🧑‍💼 Customer</span>
                            )}
                            {c.decision.internal_only && <span className="badge">🏢 Internal</span>}
                            {c.decision.needs_response && (
                              <span className="badge">↩️ Needs response</span>
                            )}
                            {c.permalink && (
                              <a
                                className="card-link"
                                href={c.permalink}
                                target="_blank"
                                rel="noreferrer"
                              >
                                ↗ Open in Slack
                              </a>
                            )}
                          </div>
                          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                            <button
                              className="btn btn-sm btn-primary"
                              disabled={added.has(c.id)}
                              onClick={() => void addToBoard(c)}
                            >
                              {added.has(c.id) ? "✓ Added" : "📋 Add to board"}
                            </button>
                            <button
                              className="btn btn-sm"
                              disabled={marked.has(c.id)}
                              onClick={() => void markRead(c)}
                            >
                              {marked.has(c.id) ? "✓ Marked read" : "✅ Mark read"}
                            </button>
                            <button
                              className="btn btn-sm"
                              onClick={() => toggleExpand(c.id)}
                            >
                              {expanded.has(c.id) ? "Hide" : "Read messages"}
                            </button>
                          </div>
                          {expanded.has(c.id) && (
                            <div className="thread-preview">
                              {c.messages.length === 0
                                ? "No unread messages to preview."
                                : c.messages
                                    .map((m) => `${m.author}: ${m.text}`)
                                    .join("\n\n")}
                            </div>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                );
              })}
          </div>
        ))}
    </div>
  );
}
