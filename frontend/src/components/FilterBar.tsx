import {
  TASK_SOURCES,
  TASK_STATUSES,
  TASK_STATUS_LABELS,
  type Tag,
  type TaskFilters,
  type TaskSource,
  type TaskStatus,
} from "../types";

interface Props {
  filters: TaskFilters;
  onChange: (next: TaskFilters) => void;
  assignees: string[];
  workstreams: string[];
  tags: Tag[];
}

export function FilterBar({ filters, onChange, assignees, workstreams, tags }: Props) {
  const set = <K extends keyof TaskFilters>(key: K, value: TaskFilters[K]) =>
    onChange({ ...filters, [key]: value });

  return (
    <div className="filters">
      <label>
        Search
        <input
          type="search"
          placeholder="Filter tasks…"
          value={filters.search}
          onChange={(e) => set("search", e.target.value)}
        />
      </label>

      <label>
        Person
        <select
          value={filters.assignee ?? ""}
          onChange={(e) => set("assignee", e.target.value || null)}
        >
          <option value="">All</option>
          {assignees.map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </select>
      </label>

      <label>
        Workstream
        <select
          value={filters.workstream ?? ""}
          onChange={(e) => set("workstream", e.target.value || null)}
        >
          <option value="">All</option>
          {workstreams.map((w) => (
            <option key={w} value={w}>
              {w}
            </option>
          ))}
          <option value="unassigned">unassigned</option>
        </select>
      </label>

      <label>
        Status
        <select
          value={filters.status ?? ""}
          onChange={(e) => set("status", (e.target.value || null) as TaskStatus | null)}
        >
          <option value="">All</option>
          {TASK_STATUSES.map((s) => (
            <option key={s} value={s}>
              {TASK_STATUS_LABELS[s]}
            </option>
          ))}
        </select>
      </label>

      <label>
        Source
        <select
          value={filters.source ?? ""}
          onChange={(e) => set("source", (e.target.value || null) as TaskSource | null)}
        >
          <option value="">All</option>
          {TASK_SOURCES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>

      <label>
        Tag
        <select
          value={filters.tags[0] ?? ""}
          onChange={(e) => set("tags", e.target.value ? [e.target.value] : [])}
        >
          <option value="">All</option>
          {tags.map((t) => (
            <option key={t.name} value={t.name}>
              {t.name}
            </option>
          ))}
        </select>
      </label>

      <label className="checkbox">
        <input
          type="checkbox"
          checked={filters.overdueOnly}
          onChange={(e) => set("overdueOnly", e.target.checked)}
        />
        🔴 Overdue only
      </label>

      <label className="checkbox">
        <input
          type="checkbox"
          checked={filters.customerOnly}
          onChange={(e) => set("customerOnly", e.target.checked)}
        />
        🧑‍💼 Customer only
      </label>
    </div>
  );
}
