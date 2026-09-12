import { useMemo, useState } from "react";
import { api } from "./api/client";
import { useAppData } from "./hooks/useAppData";
import { applyFilters, assigneeOptions } from "./utils";
import { ApiError, type Task, type TaskFilters, type ViewMode } from "./types";
import { Board } from "./components/Board/Board";
import { FilterBar } from "./components/FilterBar";
import { Triage } from "./components/Triage/Triage";
import { SlackTriage } from "./components/SlackTriage/SlackTriage";
import { WorkstreamModal } from "./components/Workstreams/WorkstreamModal";
import { TagManager } from "./components/Tags/TagManager";
import { AddTaskModal } from "./components/AddTaskModal";

const EMPTY_FILTERS: TaskFilters = {
  assignee: null,
  workstream: null,
  status: null,
  tags: [],
  source: null,
  customerOnly: false,
  overdueOnly: false,
  search: "",
};

type Tab = "board" | "triage" | "slack";
type Modal = "workstreams" | "tags" | "add" | null;

export default function App() {
  const data = useAppData();
  const [tab, setTab] = useState<Tab>("board");
  const [view, setView] = useState<ViewMode>("person");
  const [filters, setFilters] = useState<TaskFilters>(EMPTY_FILTERS);
  const [modal, setModal] = useState<Modal>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const assignees = useMemo(
    () => assigneeOptions(data.tasks, data.roster),
    [data.tasks, data.roster],
  );
  const filtered = useMemo(() => applyFilters(data.tasks, filters), [data.tasks, filters]);

  // Register a free-form tag typed on a card so its color is stable.
  const createTag = async (name: string) => {
    try {
      await api.createTag({ name });
      await data.refreshTags();
    } catch (err) {
      if (err instanceof ApiError && err.isAuth) setNotice(err.message);
    }
  };

  const sendTask = async (task: Task) => {
    try {
      const res = await api.createDraft({ task_id: task.id });
      setNotice(
        res.draft_link
          ? `Draft created for “${task.title}”. Open: ${res.draft_link}`
          : `Draft created for “${task.title}”.`,
      );
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Could not create draft.");
    }
  };

  const workstreamsChanged = () => {
    void data.refreshWorkstreams();
    void data.refreshTasks(); // rename cascade / delete reassignment
  };
  const tagsChanged = () => {
    void data.refreshTags();
    void data.refreshTasks();
  };

  return (
    <div className="app">
      <header className="app-header">
        <h1 className="app-title">📋 Action Board</h1>
        <nav className="tabs">
          <button
            className={`tab${tab === "board" ? " active" : ""}`}
            onClick={() => setTab("board")}
          >
            Board
          </button>
          <button
            className={`tab${tab === "triage" ? " active" : ""}`}
            onClick={() => setTab("triage")}
          >
            Gmail Triage
          </button>
          <button
            className={`tab${tab === "slack" ? " active" : ""}`}
            onClick={() => setTab("slack")}
          >
            Slack Triage
          </button>
        </nav>

        <span className="spacer" />

        {tab === "board" && (
          <>
            <div className="toggle-group" role="group" aria-label="Group by">
              <button
                className={view === "person" ? "active" : ""}
                onClick={() => setView("person")}
              >
                By Person
              </button>
              <button
                className={view === "workstream" ? "active" : ""}
                onClick={() => setView("workstream")}
              >
                By Workstream
              </button>
            </div>
            <button className="btn" onClick={() => setModal("add")}>
              ➕ Add task
            </button>
            <button className="btn" onClick={() => setModal("workstreams")}>
              🗂️ Workstreams
            </button>
            <button className="btn" onClick={() => setModal("tags")}>
              🏷️ Tags
            </button>
          </>
        )}
      </header>

      {data.authMessage && (
        <div className="banner banner-auth" role="alert">
          🔒 {data.authMessage}
        </div>
      )}
      {data.error && (
        <div className="banner banner-error">
          <span>{data.error}</span>
          <button className="btn btn-sm" onClick={data.clearError}>
            Dismiss
          </button>
        </div>
      )}
      {notice && (
        <div className="banner banner-error">
          <span>{notice}</span>
          <button className="btn btn-sm" onClick={() => setNotice(null)}>
            Dismiss
          </button>
        </div>
      )}

      {tab === "board" && (
        <>
          <FilterBar
            filters={filters}
            onChange={setFilters}
            assignees={assignees}
            workstreams={data.workstreams}
            tags={data.tags}
          />
          {data.loading ? (
            <div className="empty-state">Loading tasks…</div>
          ) : (
            <Board
              tasks={filtered}
              tags={data.tags}
              viewMode={view}
              workstreams={data.workstreams}
              roster={data.roster}
              onStatusChange={(id, status) => void data.moveTask(id, status)}
              onDueChange={(id, due) => void data.patchTask(id, { due_date: due })}
              onTagsChange={(id, tags) => void data.patchTask(id, { tags })}
              onDelete={(id) => void data.deleteTask(id)}
              onSend={(task) => void sendTask(task)}
              onCreateTag={createTag}
            />
          )}
        </>
      )}

      {tab === "triage" && (
        <Triage onAuth={setNotice} onAddedToBoard={() => void data.refreshTasks()} />
      )}

      {tab === "slack" && (
        <SlackTriage
          onAuth={setNotice}
          onAddedToBoard={() => void data.refreshTasks()}
          workstreams={data.workstreams}
          tags={data.tags}
          onCreateTag={createTag}
        />
      )}

      {modal === "workstreams" && (
        <WorkstreamModal
          workstreams={data.workstreams}
          onClose={() => setModal(null)}
          onChanged={workstreamsChanged}
        />
      )}
      {modal === "tags" && (
        <TagManager tags={data.tags} onClose={() => setModal(null)} onChanged={tagsChanged} />
      )}
      {modal === "add" && (
        <AddTaskModal
          assignees={assignees}
          workstreams={data.workstreams}
          onClose={() => setModal(null)}
          onCreate={data.createTask}
        />
      )}
    </div>
  );
}
