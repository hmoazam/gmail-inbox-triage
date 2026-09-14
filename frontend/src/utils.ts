import type { Task, TaskFilters } from "./types";
import { ROSTER_FALLBACK, UNASSIGNED } from "./constants";

/** Format a YYYY-MM-DD date string as e.g. "Mar 04". */
export function formatDueDate(due: string | null): string {
  if (!due) return "";
  const d = new Date(`${due}T00:00:00`);
  if (Number.isNaN(d.getTime())) return due;
  return d.toLocaleDateString(undefined, { month: "short", day: "2-digit" });
}

/** Is a YYYY-MM-DD date within the current week (today..+7 days)? */
export function isThisWeek(due: string | null): boolean {
  if (!due) return false;
  const d = new Date(`${due}T00:00:00`);
  if (Number.isNaN(d.getTime())) return false;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const in7 = new Date(today);
  in7.setDate(in7.getDate() + 7);
  return d >= today && d <= in7;
}

/** Apply the client-side filter set to a task list. */
export function applyFilters(tasks: Task[], f: TaskFilters): Task[] {
  const search = f.search.trim().toLowerCase();
  return tasks.filter((t) => {
    if (f.assignee && t.assignee !== f.assignee) return false;
    if (f.workstream && t.workstream !== f.workstream) return false;
    if (f.status && t.status !== f.status) return false;
    if (f.source && t.source !== f.source) return false;
    if (f.customerOnly && !t.customer_related) return false;
    if (f.overdueOnly && !t.overdue) return false;
    if (f.tags.length && !f.tags.every((tag) => t.tags.includes(tag))) return false;
    if (search) {
      const hay = `${t.title} ${t.context} ${t.assignee} ${t.workstream} ${t.tags.join(" ")}`.toLowerCase();
      if (!hay.includes(search)) return false;
    }
    return true;
  });
}

/** Union of the roster and any assignees present on tasks (for dropdowns). */
export function assigneeOptions(tasks: Task[], roster: string[]): string[] {
  const base = roster.length ? roster : ROSTER_FALLBACK;
  const set = new Set(base);
  for (const t of tasks) if (t.assignee && t.assignee !== UNASSIGNED) set.add(t.assignee);
  return Array.from(set);
}
