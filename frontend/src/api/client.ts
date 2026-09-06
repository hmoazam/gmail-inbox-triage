// Typed fetch wrapper. All calls hit the /api path, which Vite proxies to the
// FastAPI backend on :8000 in dev, and which FastAPI serves itself in prod.
// Never hardcode the backend origin here.

import {
  ApiError,
  type AddToBoardRequest,
  type DraftRequest,
  type DraftResponse,
  type MarkReadRequest,
  type Tag,
  type TagCreate,
  type TagPatch,
  type Task,
  type TaskCreate,
  type TaskPatch,
  type TaskSource,
  type TaskStatus,
  type TriageResponse,
} from "../types";

const BASE = "/api";

interface RequestOptions {
  method?: string;
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined | null>;
}

function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const url = `${BASE}${path}`;
  if (!query) return url;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== "") {
      params.append(key, String(value));
    }
  }
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}

/**
 * Extract a human-readable error message from a failed response. FastAPI
 * conventionally returns `{ detail: ... }`; on a 401/403 the backend returns
 * the Google re-auth message there (per the API contract), which callers
 * surface verbatim via ApiError.isAuth.
 */
async function errorMessage(res: Response): Promise<string> {
  try {
    const data = await res.json();
    if (data && typeof data === "object") {
      const detail = (data as { detail?: unknown }).detail;
      if (typeof detail === "string") return detail;
      if (detail && typeof detail === "object") {
        const msg = (detail as { message?: unknown }).message;
        if (typeof msg === "string") return msg;
      }
      const message = (data as { message?: unknown }).message;
      if (typeof message === "string") return message;
    }
  } catch {
    /* fall through to status text */
  }
  return res.statusText || `Request failed (${res.status})`;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, query } = options;

  let res: Response;
  try {
    res = await fetch(buildUrl(path, query), {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (err) {
    // Network / server-down: not an HTTP status, so status 0.
    throw new ApiError(
      err instanceof Error ? err.message : "Network error — is the backend running?",
      0,
    );
  }

  if (!res.ok) {
    throw new ApiError(await errorMessage(res), res.status);
  }

  if (res.status === 204) return undefined as T;
  // Some endpoints (DELETE) may return empty bodies with 200.
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

// ---------------------------------------------------------------------------
// Tasks
// ---------------------------------------------------------------------------

export interface ListTasksParams {
  assignee?: string;
  workstream?: string;
  status?: TaskStatus;
  source?: TaskSource;
  tags?: string[]; // match tasks containing ALL of these
}

export const api = {
  // --- Tasks ---
  listTasks(params: ListTasksParams = {}): Promise<Task[]> {
    return request<Task[]>("/tasks", {
      query: {
        assignee: params.assignee,
        workstream: params.workstream,
        status: params.status,
        source: params.source,
        // Backend accepts repeated ?tags= or a comma list; send comma list.
        tags: params.tags && params.tags.length ? params.tags.join(",") : undefined,
      },
    });
  },

  createTask(body: TaskCreate): Promise<Task> {
    return request<Task>("/tasks", { method: "POST", body });
  },

  getTask(id: string): Promise<Task> {
    return request<Task>(`/tasks/${encodeURIComponent(id)}`);
  },

  patchTask(id: string, body: TaskPatch): Promise<Task> {
    return request<Task>(`/tasks/${encodeURIComponent(id)}`, { method: "PATCH", body });
  },

  deleteTask(id: string): Promise<void> {
    return request<void>(`/tasks/${encodeURIComponent(id)}`, { method: "DELETE" });
  },

  // --- Workstreams ---
  listWorkstreams(): Promise<string[]> {
    return request<string[]>("/workstreams");
  },

  createWorkstream(name: string): Promise<string[]> {
    return request<string[]>("/workstreams", { method: "POST", body: { name } });
  },

  renameWorkstream(name: string, newName: string): Promise<string[]> {
    return request<string[]>(`/workstreams/${encodeURIComponent(name)}`, {
      method: "PATCH",
      body: { name: newName },
    });
  },

  deleteWorkstream(name: string): Promise<string[]> {
    return request<string[]>(`/workstreams/${encodeURIComponent(name)}`, { method: "DELETE" });
  },

  // --- Tags ---
  listTags(): Promise<Tag[]> {
    return request<Tag[]>("/tags");
  },

  createTag(body: TagCreate): Promise<Tag> {
    return request<Tag>("/tags", { method: "POST", body });
  },

  patchTag(name: string, body: TagPatch): Promise<Tag> {
    return request<Tag>(`/tags/${encodeURIComponent(name)}`, { method: "PATCH", body });
  },

  deleteTag(name: string): Promise<void> {
    return request<void>(`/tags/${encodeURIComponent(name)}`, { method: "DELETE" });
  },

  // --- Triage ---
  fetchTriage(opts: { maxResults?: number; query?: string } = {}): Promise<TriageResponse> {
    return request<TriageResponse>("/triage", {
      query: { max_results: opts.maxResults, query: opts.query },
    });
  },

  markRead(body: MarkReadRequest): Promise<{ marked: number }> {
    return request<{ marked: number }>("/triage/mark-read", { method: "POST", body });
  },

  addToBoard(body: AddToBoardRequest): Promise<Task> {
    return request<Task>("/triage/add-to-board", { method: "POST", body });
  },

  // --- Drafts (draft-first "send" for teammate tasks) ---
  createDraft(body: DraftRequest): Promise<DraftResponse> {
    return request<DraftResponse>("/drafts", { method: "POST", body });
  },

  // --- Ingest ---
  ingestMeet(): Promise<{ added: number }> {
    return request<{ added: number }>("/ingest/meet", { method: "POST", body: {} });
  },

  ingestTranscripts(): Promise<{ added: number }> {
    return request<{ added: number }>("/ingest/transcripts", { method: "POST", body: {} });
  },

  // --- Roster (ASSUMPTION: not in the documented contract) ---
  // Frontend needs the roster for the assignee dropdown and empty By-Person
  // lanes. If the backend exposes GET /api/roster this uses it; callers fall
  // back to a constant on failure (see useRoster).
  getRoster(): Promise<string[]> {
    return request<string[]>("/roster");
  },
};
