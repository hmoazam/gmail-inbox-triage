// TypeScript types mirroring the backend Pydantic schemas (api/schemas.py) and
// models.py `Task.as_dict()`. Source of truth: PLAN_REACT_MIGRATION.md API
// contract. Any field marked ASSUMPTION needs backend confirmation.

export const TASK_STATUSES = ["todo", "in_progress", "done"] as const;
export type TaskStatus = (typeof TASK_STATUSES)[number];

export const TASK_STATUS_LABELS: Record<TaskStatus, string> = {
  todo: "To Do",
  in_progress: "In Progress",
  done: "Done",
};

export const TASK_SOURCES = ["email", "meet", "teams", "manual"] as const;
export type TaskSource = (typeof TASK_SOURCES)[number];

// Triage bucket categories, in render order (models.py CATEGORIES).
export const TRIAGE_CATEGORIES = ["action_required", "useful", "other"] as const;
export type TriageCategory = (typeof TRIAGE_CATEGORIES)[number];

export const TRIAGE_CATEGORY_LABELS: Record<TriageCategory, string> = {
  action_required: "Action Required",
  useful: "Useful",
  other: "Other",
};

/**
 * One Action Board task. Mirrors models.py `Task.as_dict()` plus the derived
 * `overdue` flag and the new `tags` list.
 *
 * NOTE: models.py `as_dict()` intentionally EXCLUDES `overdue` (it is a derived
 * property). The API contract and this frontend both require it, so the backend
 * Pydantic schema must add `overdue` to the serialized task. Flagged as an
 * assumption to reconcile with the backend teammate.
 */
export interface Task {
  id: string;
  title: string;
  assignee: string; // roster name or "unassigned"
  workstream: string; // workstream name or "unassigned"
  status: TaskStatus;
  source: TaskSource;
  created_at: string; // ISO-8601 UTC
  updated_at: string; // ISO-8601 UTC
  due_date: string | null; // YYYY-MM-DD
  source_ref: string | null; // thread_id (email) or file_id (transcript)
  source_link: string | null; // Gmail thread URL or Drive doc URL
  context: string;
  customer_related: boolean;
  confidence: number; // 0.0–1.0
  completed_at: string | null; // ISO-8601 UTC
  tags: string[];
  overdue: boolean; // derived (see note above)
}

/** Fields accepted when creating a task (POST /api/tasks). */
export interface TaskCreate {
  title: string;
  assignee?: string;
  workstream?: string;
  status?: TaskStatus;
  source?: TaskSource;
  due_date?: string | null;
  source_ref?: string | null;
  source_link?: string | null;
  context?: string;
  customer_related?: boolean;
  confidence?: number;
  tags?: string[];
}

/** Fields accepted on PATCH /api/tasks/{id} (all optional). */
export interface TaskPatch {
  title?: string;
  assignee?: string;
  workstream?: string;
  status?: TaskStatus;
  due_date?: string | null;
  context?: string;
  tags?: string[];
  customer_related?: boolean;
}

/** A tag registry entry: name → hex color (tag_store.py). */
export interface Tag {
  name: string;
  color: string; // hex, e.g. "#3b82f6"
}

export interface TagCreate {
  name: string;
  color?: string;
}

export interface TagPatch {
  // rename and/or recolor; backend maps to rename()/set_color()
  name?: string;
  color?: string;
}

/** Classifier output for one thread — NESTED under thread.decision (models.py). */
export interface ThreadDecision {
  thread_id?: string;
  category: TriageCategory;
  summary: string; // one-line why / what it is
  action_on_me: string | null; // concrete action for the account owner
  customer_related: boolean;
  internal_only: boolean;
  needs_response: boolean;
  confidence: number;
}

/** One message within a triage thread (mirrors backend ThreadMessageOut). */
export interface TriageMessage {
  id: string;
  sender?: string; // raw From header, "Name <addr>"
  sender_email?: string; // parsed bare address, lowercased
  to?: string;
  date?: string;
  body?: string;
  from_me?: boolean;
}

/**
 * One thread returned by GET /api/triage. The classification is nested under
 * `decision`; last-message from/date are derived from `messages[]`.
 */
export interface TriageThread {
  thread_id: string;
  subject: string;
  unread: boolean;
  participants: string[]; // bare email addresses
  message_count: number;
  messages: TriageMessage[];
  source_link: string | null; // Gmail thread URL
  decision: ThreadDecision;
}

export interface TriageResponse {
  threads: TriageThread[];
  usage?: Record<string, unknown>;
}

/** Request body for POST /api/triage/add-to-board — the thread's decision fields. */
export interface AddToBoardRequest {
  thread_id: string;
  category: TriageCategory;
  summary: string;
  action_on_me: string | null;
  customer_related: boolean;
  internal_only: boolean;
  needs_response: boolean;
  confidence: number;
}

/** Response from POST /api/triage/add-to-board. created:false = already on board. */
export interface AddToBoardResponse {
  created: boolean;
  task: Task;
}

/** Request body for POST /api/triage/mark-read — Gmail message ids, not thread ids. */
export interface MarkReadRequest {
  message_ids: string[];
}

// ---------------------------------------------------------------------------
// Slack triage (GET /api/slack-triage and its actions).
// Grouped by CATEGORY (Direct Messages first, then the configured channel
// groups). Within a group, conversations carry the SAME `decision` shape as
// the Gmail triage thread, so they bucket into action_required/useful/other.
// ---------------------------------------------------------------------------

/** One unread message inside a Slack conversation (mirrors backend). */
export interface SlackMessage {
  author: string; // display name of the sender
  ts: string; // Slack message timestamp ("1699...")
  text: string;
}

/**
 * One Slack conversation returned by GET /api/slack-triage. `decision` reuses
 * the Gmail ThreadDecision shape (category/summary/action_on_me/…). Send
 * `latest_ts` as the `ts` when marking read.
 */
export interface SlackConversation {
  id: string; // Slack channel/DM id (e.g. "C0…" or "D0…")
  kind: "im" | "channel";
  name: string; // channel name, or DM user's real name
  unread_count: number;
  messages: SlackMessage[];
  latest_ts: string; // ts of the most recent unread message
  permalink: string; // deep link into Slack
  decision: ThreadDecision;
}

/** One category group in the Slack triage response, rendered in array order. */
export interface SlackGroup {
  category: string; // "Direct Messages", "AI Gateway Accounts", "Team", …
  conversations: SlackConversation[];
}

/** GET /api/slack-triage → groups in display order + optional usage stats. */
export interface SlackTriageResponse {
  groups: SlackGroup[];
  usage?: Record<string, unknown>;
}

/** POST /api/slack-triage/mark-read — send the conversation's latest_ts as ts. */
export interface SlackMarkReadRequest {
  channel_id: string;
  ts: string;
}

/** Response from POST /api/slack-triage/mark-read. */
export interface SlackMarkReadResponse {
  ok: boolean;
}

/**
 * POST /api/slack-triage/add-to-board. Mirrors the contract exactly: no
 * `internal_only`/`category` (unlike the Gmail body) — the backend derives the
 * task from these fields. Reuses AddToBoardResponse ({ created, task }).
 */
export interface SlackAddToBoardRequest {
  channel_id: string;
  name: string;
  permalink: string;
  summary: string;
  action_on_me: string | null;
  customer_related: boolean;
  needs_response: boolean;
  confidence: number;
  assignee?: string;
  workstream?: string;
  tags?: string[];
}

/** One roster member from GET /api/roster. */
export interface RosterMember {
  name: string;
  email: string | null;
}

/** Return shape of workstream POST/PATCH/DELETE mutations. */
export interface WorkstreamMutationResult {
  ok: boolean;
  workstreams: string[];
  tasks_updated: number;
}

/** Request body for POST /api/drafts (draft-first "send"). */
export interface DraftRequest {
  task_id: string;
}

export interface DraftResponse {
  draft_id: string;
  draft_link?: string | null;
}

/** Board grouping mode — client-side pivot over the same task data. */
export type ViewMode = "person" | "workstream";

/** Client-side filter state applied to the task list. */
export interface TaskFilters {
  assignee: string | null;
  workstream: string | null;
  status: TaskStatus | null;
  tags: string[];
  source: TaskSource | null;
  customerOnly: boolean;
  overdueOnly: boolean;
  search: string;
}

/** Error carrying the backend's re-auth message on a 401/403. */
export class ApiError extends Error {
  status: number;
  /** True for 401/403 — Google auth expired; surface the message verbatim. */
  isAuth: boolean;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.isAuth = status === 401 || status === 403;
  }
}
