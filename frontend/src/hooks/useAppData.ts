import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { ApiError, type Tag, type Task, type TaskCreate, type TaskPatch } from "../types";
import { ROSTER_FALLBACK } from "../constants";

interface AppData {
  tasks: Task[];
  workstreams: string[];
  tags: Tag[];
  roster: string[];
  loading: boolean;
  /** Google-auth-expired message from a 401/403; surface verbatim. */
  authMessage: string | null;
  /** Transient non-auth error message. */
  error: string | null;
  clearError: () => void;
  refreshTasks: () => Promise<void>;
  refreshWorkstreams: () => Promise<void>;
  refreshTags: () => Promise<void>;
  createTask: (body: TaskCreate) => Promise<Task | null>;
  patchTask: (id: string, body: TaskPatch) => Promise<Task | null>;
  deleteTask: (id: string) => Promise<boolean>;
  /** Optimistic status change (card status control); reverts on failure. */
  moveTask: (id: string, status: Task["status"]) => Promise<void>;
  /** Optimistic planned-day move for DnD between day columns; reverts on failure. */
  moveTaskDay: (id: string, plannedDay: Task["planned_day"]) => Promise<void>;
}

export function useAppData(): AppData {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [workstreams, setWorkstreams] = useState<string[]>([]);
  const [tags, setTags] = useState<Tag[]>([]);
  const [roster, setRoster] = useState<string[]>(ROSTER_FALLBACK);
  const [loading, setLoading] = useState(true);
  const [authMessage, setAuthMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleErr = useCallback((err: unknown) => {
    if (err instanceof ApiError && err.isAuth) {
      setAuthMessage(err.message);
    } else if (err instanceof ApiError) {
      setError(err.message);
    } else {
      setError(err instanceof Error ? err.message : "Unexpected error");
    }
  }, []);

  const refreshTasks = useCallback(async () => {
    try {
      setTasks(await api.listTasks());
      setAuthMessage(null);
    } catch (err) {
      handleErr(err);
    }
  }, [handleErr]);

  const refreshWorkstreams = useCallback(async () => {
    try {
      setWorkstreams(await api.listWorkstreams());
    } catch (err) {
      handleErr(err);
    }
  }, [handleErr]);

  const refreshTags = useCallback(async () => {
    try {
      setTags(await api.listTags());
    } catch (err) {
      handleErr(err);
    }
  }, [handleErr]);

  const refreshRoster = useCallback(async () => {
    // GET /api/roster → [{name, email}]; map to names. Fall back on failure.
    try {
      const members = await api.getRoster();
      const names = members.map((m) => m.name).filter(Boolean);
      if (names.length) setRoster(names);
    } catch {
      /* keep fallback */
    }
  }, []);

  useEffect(() => {
    (async () => {
      setLoading(true);
      await Promise.all([refreshTasks(), refreshWorkstreams(), refreshTags(), refreshRoster()]);
      setLoading(false);
    })();
  }, [refreshTasks, refreshWorkstreams, refreshTags, refreshRoster]);

  const createTask = useCallback(
    async (body: TaskCreate): Promise<Task | null> => {
      try {
        const created = await api.createTask(body);
        setTasks((prev) => [created, ...prev]);
        return created;
      } catch (err) {
        handleErr(err);
        return null;
      }
    },
    [handleErr],
  );

  const patchTask = useCallback(
    async (id: string, body: TaskPatch): Promise<Task | null> => {
      try {
        const updated = await api.patchTask(id, body);
        setTasks((prev) => prev.map((t) => (t.id === id ? updated : t)));
        return updated;
      } catch (err) {
        handleErr(err);
        return null;
      }
    },
    [handleErr],
  );

  const deleteTask = useCallback(
    async (id: string): Promise<boolean> => {
      const prev = tasks;
      setTasks((cur) => cur.filter((t) => t.id !== id)); // optimistic
      try {
        await api.deleteTask(id);
        return true;
      } catch (err) {
        setTasks(prev); // revert
        handleErr(err);
        return false;
      }
    },
    [tasks, handleErr],
  );

  const moveTask = useCallback(
    async (id: string, status: Task["status"]): Promise<void> => {
      const prev = tasks;
      // Optimistic: update status locally immediately for a snappy DnD feel.
      setTasks((cur) => cur.map((t) => (t.id === id ? { ...t, status } : t)));
      try {
        const updated = await api.patchTask(id, { status });
        setTasks((cur) => cur.map((t) => (t.id === id ? updated : t)));
      } catch (err) {
        setTasks(prev); // revert on failure
        handleErr(err);
      }
    },
    [tasks, handleErr],
  );

  const moveTaskDay = useCallback(
    async (id: string, plannedDay: Task["planned_day"]): Promise<void> => {
      const prev = tasks;
      // Optimistic: reflect the new day column immediately for a snappy DnD feel.
      setTasks((cur) => cur.map((t) => (t.id === id ? { ...t, planned_day: plannedDay } : t)));
      try {
        const updated = await api.patchTask(id, { planned_day: plannedDay });
        setTasks((cur) => cur.map((t) => (t.id === id ? updated : t)));
      } catch (err) {
        setTasks(prev); // revert on failure
        handleErr(err);
      }
    },
    [tasks, handleErr],
  );

  return {
    tasks,
    workstreams,
    tags,
    roster,
    loading,
    authMessage,
    error,
    clearError: () => setError(null),
    refreshTasks,
    refreshWorkstreams,
    refreshTags,
    createTask,
    patchTask,
    deleteTask,
    moveTask,
    moveTaskDay,
  };
}
