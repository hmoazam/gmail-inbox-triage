import type { TaskSource } from "./types";

// Fallback roster mirroring config.py TEAM_ROSTER. The backend does not expose
// a roster endpoint in the documented contract (flagged as an assumption), so
// this is the fallback when GET /api/roster is unavailable. "Hanna" is the
// account owner ("me"); the rest are teammates whose tasks can be "sent".
export const ROSTER_FALLBACK = ["Hanna", "Rick", "Ghaj", "Gustav", "Subash"];

// The account owner — rendered first in By-Person view, never "sendable".
export const ME = "Hanna";

export const UNASSIGNED = "unassigned";

// Source → emoji icon (matches the Streamlit app).
export const SOURCE_ICONS: Record<TaskSource, string> = {
  email: "📧",
  meet: "🎥",
  teams: "🧑‍💻",
  manual: "✍️",
  slack: "💬",
};

// Default palette used when a tag has no color yet (mirrors tag_store's palette
// intent). The backend is the source of truth for colors; this is display-only.
export const TAG_PALETTE = [
  "#3b82f6",
  "#ef4444",
  "#10b981",
  "#f59e0b",
  "#8b5cf6",
  "#ec4899",
  "#14b8a6",
  "#f97316",
  "#6366f1",
  "#84cc16",
];

/** Stable color for a tag name absent from the registry (display fallback). */
export function fallbackColor(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  }
  return TAG_PALETTE[hash % TAG_PALETTE.length];
}
