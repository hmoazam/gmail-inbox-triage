"""Gmail inbox triage + action board — local Streamlit app.

Two tabs:
  📋 Board  — persistent Trello/Jira-inspired task board (primary view).
              Pulls tasks from task_store; view by Person or Workstream.
  📧 Inbox Triage — original 3-bucket view (Action Required / Useful / Other)
                    with an "Add to board" affordance on action-required threads.

Auth: `gcloud auth application-default login` (Gmail) before use.
      Claude Code SDK uses your already-authenticated local `claude` CLI.
Run:  streamlit run app.py
"""
from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from config import get_settings
from gmail_client import GmailClient, AuthError
from classifier import classify_threads
import decision_cache
from models import (
    CATEGORY_ACTION, CATEGORY_USEFUL, CATEGORY_OTHER, EmailThread, ThreadDecision,
)

# ---------------------------------------------------------------------------
# Optional board imports — graceful degradation until branches merge
# ---------------------------------------------------------------------------
try:
    from task_store import new_task, add, get, update, delete, list_tasks, find_by_source  # noqa: F401
    from models import Task, TASK_STATUSES, TASK_SOURCES
    _BOARD_AVAILABLE = True
except ImportError:
    _BOARD_AVAILABLE = False
    TASK_STATUSES = ("todo", "in_progress", "done")  # type: ignore[assignment]
    TASK_SOURCES = ("email", "meet", "teams", "manual")  # type: ignore[assignment]

try:
    from config import TEAM_ROSTER  # type: ignore[attr-defined]
    ROSTER_NAMES: list[str] = [r["name"] for r in TEAM_ROSTER]
    TEAMMATE_ORDER: list[str] = [r["name"] for r in TEAM_ROSTER if r["name"] != "Hanna"]
except (ImportError, AttributeError):
    TEAM_ROSTER = [
        {"name": "Hanna", "email": ""},
        {"name": "Rick", "email": ""},
        {"name": "Ghaj", "email": ""},
        {"name": "Gustav", "email": ""},
        {"name": "Subash", "email": ""},
    ]
    ROSTER_NAMES = [r["name"] for r in TEAM_ROSTER]
    TEAMMATE_ORDER = [r["name"] for r in TEAM_ROSTER if r["name"] != "Hanna"]

# Workstreams are user-editable and persist in workstream_store (seeded from
# config.WORKSTREAMS on first use).  Always read the live list via
# get_workstreams() rather than a module-level constant so edits take effect.
try:
    import workstream_store  # type: ignore[import]
    _WORKSTREAM_STORE_AVAILABLE = True
except ImportError:
    _WORKSTREAM_STORE_AVAILABLE = False


def get_workstreams() -> list[str]:
    """The current, live workstream list (falls back to a static default)."""
    if _WORKSTREAM_STORE_AVAILABLE:
        return workstream_store.list_workstreams()
    return ["Customer Engagements", "Internal Projects", "Admin", "Hiring", "Other"]

try:
    from classifier import thread_decision_to_task as _thread_decision_to_task  # type: ignore[attr-defined]
    _BRIDGE_AVAILABLE = True
except ImportError:
    _BRIDGE_AVAILABLE = False

# ---------------------------------------------------------------------------
# Page config — must be the first st.* call in the module
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Gmail Action Board", page_icon="📋", layout="wide")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SECTIONS = [
    (CATEGORY_ACTION, "🚨 Action Required", "Threads with a concrete action on you."),
    (CATEGORY_USEFUL, "💡 Useful", "No action needed, but worth being aware of."),
    (CATEGORY_OTHER,  "🗂️ Other", "Everything else."),
]
SOURCE_ICONS = {"email": "📧", "meet": "🎥", "teams": "🧑‍💻", "manual": "✍️"}
STATUS_LABELS = {"todo": "To Do", "in_progress": "In Progress", "done": "Done"}


# ---------------------------------------------------------------------------
# Shared Gmail client
# ---------------------------------------------------------------------------
@st.cache_resource
def get_client() -> GmailClient:
    return GmailClient()


# ===========================================================================
# EXISTING TRIAGE HELPERS  (preserved verbatim except render_section, which
# gains one board-bridge button for action-required rows)
# ===========================================================================

def fetch_and_classify(max_results: int | None, query: str) -> None:
    settings = get_settings()
    client = get_client()

    cap_label = f"(cap {max_results})" if max_results else "(all)"
    fetch_bar = st.progress(0.0, text=f"Fetching inbox threads… {cap_label}")
    ids = client.list_inbox_threads(max_results=max_results, query=query)
    if not ids:
        fetch_bar.empty()
        st.session_state.rows = []
        st.warning("No threads matched. Try a different filter.")
        return

    def on_fetch(done: int, total: int) -> None:
        fetch_bar.progress(done / total, text=f"Fetching threads… {done}/{total}")

    threads = client.get_threads(ids, progress=on_fetch)
    fetch_bar.empty()

    class_bar = st.progress(0.0, text="Triaging threads…")

    def on_class(done: int, total: int, instant: int, claude: int) -> None:
        pct = done / total if total else 1.0
        if claude == 0:
            msg = f"Checking rules & cache… {done}/{total}"
        else:
            msg = (f"Reused {instant} cached/rule result(s) · Claude reading {claude} new "
                   f"· {done}/{total} done")
        class_bar.progress(pct, text=msg)

    try:
        decisions, stats = classify_threads(threads, settings, progress=on_class)
    except Exception as exc:  # noqa: BLE001
        class_bar.empty()
        st.error(
            f"Classification failed — likely a backend auth error.\n\n"
            f"**{type(exc).__name__}:** {exc}\n\n"
            f"For `claude_cli`: make sure the `claude` CLI is authenticated.\n"
            f"For `databricks_fm`: check your `{settings['databricks_profile']}` "
            f"profile is still valid."
        )
        return

    class_bar.empty()
    st.session_state.last_usage = stats.as_dict()
    by_id = {d.thread_id: d for d in decisions}
    st.session_state.rows = [
        {"thread": t, "decision": by_id[t.thread_id], "selected": False}
        for t in threads
    ]


def sidebar() -> tuple[int | None, str]:
    s = get_settings()
    st.sidebar.header("⚙️ Settings")
    st.sidebar.caption(
        f"User: **{s['user_name'] or s['user_email'] or 'auto-detected from Gmail'}**"
    )
    st.sidebar.caption(f"Backend: **{s['backend']}**")
    st.sidebar.caption(f"Model: **{s['model'] or 'CLI default'}**")
    st.sidebar.caption(
        f"Internal domain: **{('@' + s['internal_domain']) if s['internal_domain'] else 'not set'}**"
    )
    query = st.sidebar.text_input("Gmail search filter", value="in:inbox is:unread")
    st.sidebar.caption("e.g. `in:inbox is:unread`, `in:inbox newer_than:7d`")
    cap_on = st.sidebar.toggle("Cap results (for testing)", value=False)
    max_results: int | None = None
    if cap_on:
        max_results = st.sidebar.slider("Max threads", 5, 200, 30, step=5)
    st.sidebar.divider()
    st.sidebar.caption(
        "🔒 Triage tab: the only action is **mark as read**. "
        "Nothing is moved, archived, or deleted."
    )
    n_cached = decision_cache.count()
    st.sidebar.caption(
        f"Classification cache: **{n_cached}** thread(s). "
        "Unread threads with no new messages reuse their prior result."
    )
    if st.sidebar.button("🗑️ Clear cache (re-classify everything)", key="clear_cache"):
        decision_cache.clear()
        st.rerun()

    usage = st.session_state.get("last_usage")
    if usage:
        st.sidebar.divider()
        st.sidebar.markdown("**Last run — token usage**")
        st.sidebar.caption(
            f"Claude calls: **{usage['claude_calls']}**  \n"
            f"Input tokens: **{usage['input_tokens']:,}**  \n"
            f"Output tokens: **{usage['output_tokens']:,}**  \n"
            f"Total tokens: **{usage['total_tokens']:,}**"
        )
        if usage["cost_usd"] is not None:
            st.sidebar.caption(f"Estimated cost: **${usage['cost_usd']:.4f}**")
        else:
            st.sidebar.caption("Cost: _not reported by this backend_")
    return max_results, query


def badges(t: EmailThread, d: ThreadDecision) -> str:
    out = []
    if d.customer_related:
        out.append("🧑‍💼 Customer")
    if d.internal_only:
        out.append("🏢 Internal")
    if d.needs_response:
        out.append("↩️ Needs response")
    if t.unread:
        out.append("🔵 Unread")
    if t.unsubscribe:
        out.append("📭 Bulk")
    return "  ·  ".join(out) if out else "—"


def render_row(key: str, row: dict) -> None:
    t, d = row["thread"], row["decision"]
    with st.container(border=True):
        head = st.columns([0.06, 0.94])
        skey = row.get("sel_key", f"sel_{key}")
        row["selected"] = head[0].checkbox(
            "select", value=st.session_state.get(skey, row["selected"]),
            key=skey, label_visibility="collapsed",
        )
        with head[1]:
            unread_dot = "🔵 " if t.unread else ""
            st.markdown(f"{unread_dot}**{t.subject}**")
            parts = ", ".join(t.participants[:4]) or "—"
            more = f" +{len(t.participants) - 4}" if len(t.participants) > 4 else ""
            n = len(t.messages)
            st.caption(f"{parts}{more}  ·  {n} message{'s' if n != 1 else ''}")

        if d.category == CATEGORY_ACTION and d.action_on_me:
            st.markdown(f"**🚨 Action:** {d.action_on_me}")
        if d.summary:
            conf = f"  ·  {d.confidence:.0%}" if d.confidence else ""
            st.caption(f"💬 {d.summary}{conf}")
        st.caption(badges(t, d))

        with st.expander("Read thread"):
            for m in t.messages:
                who = "**ME**" if m.from_me else f"**{m.sender}**"
                st.markdown(f"{who} · _{m.date}_")
                st.text((m.body or "").strip()[:2000] or "(empty)")
                st.divider()


def mark_selected_read(rows: list[dict]) -> None:
    for r in rows:
        sk = r.get("sel_key")
        if sk and sk in st.session_state:
            r["selected"] = st.session_state[sk]
    client = get_client()
    chosen = [r for r in rows if r["selected"]]
    if not chosen:
        st.info("No threads selected.")
        return
    msg_ids: list[str] = []
    for r in chosen:
        msg_ids.extend(r["thread"].message_ids)
    try:
        client.mark_read(msg_ids)
    except AuthError as exc:
        st.error(str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        st.error(f"Failed to mark as read: {exc}")
        return
    rows = st.session_state.get("rows", [])
    for r in chosen:
        r["thread"].unread = False
        r["selected"] = False
        if r in rows:
            rows.remove(r)
    st.success(f"Marked {len(chosen)} thread(s) as read.")


def render_section(cat: str, title: str, help_text: str, rows: list[dict]) -> None:
    items = [r for r in rows if r["decision"].category == cat]
    st.subheader(f"{title}  ({len(items)})")
    st.caption(help_text)
    if not items:
        st.caption("_None._")
        return
    bar = st.columns([0.25, 0.25, 0.5])
    if bar[0].button("Select all", key=f"selall_{cat}"):
        for i, r in enumerate(items):
            st.session_state[f"sel_{cat}_{i}"] = True
            r["selected"] = True
    if bar[1].button("Clear", key=f"clr_{cat}"):
        for i, r in enumerate(items):
            st.session_state[f"sel_{cat}_{i}"] = False
            r["selected"] = False
    for i, row in enumerate(items):
        render_row(f"{cat}_{i}", row)
        # Board affordance: action-required threads can be bridged to the board.
        if cat == CATEGORY_ACTION:
            _render_add_to_board_button(row, f"{cat}_{i}")


def _do_mark_read(rows: list[dict]) -> None:
    mark_selected_read(rows)


# ===========================================================================
# BOARD HELPERS
# ===========================================================================

def _render_send_button(task: "Task") -> None:
    """Create a Gmail draft addressed to a teammate.  Never sends directly."""
    email = next((r["email"] for r in TEAM_ROSTER if r["name"] == task.assignee), "")
    if not email:
        st.button(
            "✉️ Send",
            key=f"send_{task.id}",
            disabled=True,
            help=f"No email on file for {task.assignee}. Set CLEANUP_ROSTER_PATH to enable.",
        )
        return
    if st.button("✉️ Send", key=f"send_{task.id}", help="Create Gmail draft (never sends directly)"):
        try:
            client = get_client()
            subject = f"Action needed: {task.title}"
            body = task.context or task.title
            client.create_draft(to=email, subject=subject, body=body)
            st.success("Draft created — check Gmail Drafts to review before sending.")
        except AttributeError:
            st.error("create_draft not yet available — ingestion branch not merged.")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not create draft: {exc}")


def _render_add_to_board_button(row: dict, key: str) -> None:
    """'Add to board' affordance shown under action-required triage rows."""
    if not _BOARD_AVAILABLE:
        return
    t, d = row["thread"], row["decision"]
    if not d.action_on_me:
        return
    if st.button("📋 Add to board", key=f"addboard_{key}"):
        try:
            # Dedup check first — avoid duplicates on repeated clicks.
            existing = find_by_source("email", d.thread_id, d.action_on_me)
            if existing:
                st.info(f'Already on board: "{existing.title}"')
                return
            if _BRIDGE_AVAILABLE:
                # Correct signature: (decision, thread, settings) -> Task | None
                task = _thread_decision_to_task(d, t, get_settings())
                if task is None:
                    st.warning("Thread is not action-required; nothing to add.")
                    return
            else:
                # Lightweight fallback: build a minimal Task from the thread decision.
                task = new_task(
                    title=d.action_on_me,
                    assignee="Hanna",
                    workstream="unassigned",
                    source="email",
                    source_ref=t.thread_id,
                    source_link=f"https://mail.google.com/mail/#inbox/{t.thread_id}",
                    customer_related=d.customer_related,
                    confidence=d.confidence,
                    context=d.summary or "",
                )
            add(task)
            st.success(f'Added to board: "{task.title}"')
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not add to board: {exc}")


def render_card(task: "Task", tag_field: str, is_teammate: bool) -> None:
    """Render a single Kanban card with actions.

    tag_field: "workstream" (By Person view) or "assignee" (By Workstream view).
    is_teammate: True when the swimlane owner is Rick/Ghaj/Gustav/Subash.
    """
    src_icon = SOURCE_ICONS.get(task.source, "✍️")
    with st.container(border=True):
        # Title
        st.markdown(f"{src_icon} **{task.title}**")

        # Due date — red when overdue
        if task.due_date:
            due_str = task.due_date.strftime("%b %-d")
            if task.overdue:
                st.markdown(f":red[📅 {due_str} — overdue]")
            else:
                st.caption(f"📅 {due_str}")

        # Customer badge + workstream/assignee tag
        badge_parts = []
        if task.customer_related:
            badge_parts.append("🧑‍💼 Customer")
        tag_val = getattr(task, tag_field, None) or "unassigned"
        badge_parts.append(f"🏷️ {tag_val}")
        st.caption("  ·  ".join(badge_parts))

        # Source link
        if task.source_link:
            st.markdown(f"[↗ View source]({task.source_link})")

        # Actions row: status | delete | edit-due toggle
        a1, a2, a3 = st.columns([0.55, 0.2, 0.25])

        status_opts = list(TASK_STATUSES)
        cur_idx = status_opts.index(task.status) if task.status in status_opts else 0
        new_status = a1.selectbox(
            "Status",
            options=status_opts,
            index=cur_idx,
            key=f"status_{task.id}",
            label_visibility="collapsed",
            format_func=STATUS_LABELS.get,  # type: ignore[arg-type]
        )

        # Delete — checked before status so st.rerun() prevents double-update.
        if a2.button("🗑️", key=f"del_{task.id}", help="Delete task"):
            delete(task.id)
            st.rerun()

        # Edit-due-date toggle
        edit_key = f"_edit_due_{task.id}"
        if a3.button("📅 Edit", key=f"editdue_{task.id}", help="Edit due date"):
            st.session_state[edit_key] = not st.session_state.get(edit_key, False)

        # Inline due-date editor (shown when toggled)
        if st.session_state.get(edit_key, False):
            new_due = st.date_input(
                "Due date",
                value=task.due_date,
                key=f"due_input_{task.id}",
                label_visibility="collapsed",
            )
            if st.button("Save date", key=f"savedue_{task.id}"):
                update(task.id, due_date=new_due)
                st.session_state[edit_key] = False
                st.rerun()

        # Apply status change (skipped if delete/save already called st.rerun)
        if new_status != task.status:
            update(task.id, status=new_status)
            st.rerun()

        # Send button (teammates only)
        if is_teammate:
            _render_send_button(task)


def render_swimlane(
    label: str,
    tasks: list["Task"],
    tag_field: str,
    is_teammate: bool,
) -> None:
    """Four Kanban columns for a single person/workstream lane."""
    overdue = [t for t in tasks if t.overdue]
    todo    = [t for t in tasks if t.status == "todo"        and not t.overdue]
    in_prog = [t for t in tasks if t.status == "in_progress" and not t.overdue]
    done    = [t for t in tasks if t.status == "done"]

    headings = ["🔴 Overdue", "📋 To Do", "⚡ In Progress", "✅ Done"]
    buckets  = [overdue, todo, in_prog, done]

    cols = st.columns(4)
    for col, heading, bucket in zip(cols, headings, buckets):
        with col:
            st.markdown(f"**{heading}** ({len(bucket)})")
            for task in bucket:
                render_card(task, tag_field, is_teammate)


def group_tasks_by(tasks: list["Task"], group_by: str) -> dict[str, list["Task"]]:
    """Bucket tasks by assignee or workstream."""
    groups: dict[str, list] = {}
    for task in tasks:
        key = (getattr(task, group_by, None) or "unassigned")
        groups.setdefault(key, []).append(task)
    return groups


def apply_filters(tasks: list["Task"], filters: dict) -> list["Task"]:
    result = tasks
    if filters.get("source"):
        result = [t for t in result if t.source in filters["source"]]
    if filters.get("person"):
        result = [t for t in result if t.assignee in filters["person"]]
    if filters.get("workstream"):
        result = [t for t in result if t.workstream in filters["workstream"]]
    today = date.today()
    due = filters.get("due", "All")
    if due == "Overdue":
        result = [t for t in result if t.overdue]
    elif due == "This week":
        week_end = today + timedelta(days=7)
        result = [t for t in result if t.due_date and today <= t.due_date <= week_end]
    elif due == "No due date":
        result = [t for t in result if not t.due_date]
    return result


def render_filter_bar() -> dict:
    with st.expander("🔍 Filters", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        source_f   = c1.multiselect("Source",     list(TASK_SOURCES),  key="filter_source")
        person_f   = c2.multiselect("Person",     ROSTER_NAMES,        key="filter_person")
        ws_f       = c3.multiselect("Workstream", get_workstreams(),   key="filter_workstream")
        due_f      = c4.radio(
            "Due", ["All", "Overdue", "This week", "No due date"],
            key="filter_due",
        )
    return {"source": source_f, "person": person_f, "workstream": ws_f, "due": due_f}


def render_add_task_form() -> None:
    with st.expander("➕ Add task manually", expanded=False):
        with st.form("add_task_form", clear_on_submit=True):
            title = st.text_input("Action / title *")
            c1, c2 = st.columns(2)
            assignee   = c1.selectbox("Assignee",    options=ROSTER_NAMES)
            workstream = c2.selectbox(
                "Workstream",
                options=get_workstreams() + ["unassigned"],
            )
            c3, c4 = st.columns(2)
            due_date = c3.date_input("Due date (optional)", value=None)
            status   = c4.selectbox(
                "Status",
                options=list(TASK_STATUSES),
                format_func=STATUS_LABELS.get,  # type: ignore[arg-type]
            )
            submitted = st.form_submit_button("Add task")

        if submitted:
            if title.strip():
                task = new_task(
                    title=title.strip(),
                    assignee=assignee,
                    workstream=workstream,
                    status=status,
                    source="manual",
                    due_date=due_date if due_date else None,
                )
                add(task)
                st.success(f'Added: "{task.title}"')
                st.rerun()
            else:
                st.warning("Title is required.")


def render_manage_workstreams() -> None:
    """Add / rename / delete workstreams; edits persist and cascade to tasks."""
    if not _WORKSTREAM_STORE_AVAILABLE:
        return
    current = get_workstreams()
    with st.expander(f"🏷️ Manage workstreams ({len(current)})", expanded=False):
        # --- add ---
        with st.form("add_workstream_form", clear_on_submit=True):
            new_ws = st.text_input("New workstream name")
            if st.form_submit_button("Add workstream"):
                name = new_ws.strip()
                if not name:
                    st.warning("Name is required.")
                elif workstream_store.add(name):
                    st.success(f'Added workstream "{name}".')
                    st.rerun()
                else:
                    st.warning(f'"{name}" already exists (or is reserved).')

        if not current:
            st.caption("_No workstreams yet — add one above._")
            return

        st.caption(
            "Renaming updates every task on that workstream. Deleting moves its "
            "tasks to **unassigned**."
        )
        # --- rename / delete each existing workstream ---
        for ws in current:
            c1, c2, c3 = st.columns([0.6, 0.2, 0.2])
            new_name = c1.text_input(
                "name", value=ws, key=f"ws_edit_{ws}", label_visibility="collapsed",
            )
            if c2.button("Rename", key=f"ws_rename_{ws}"):
                target = new_name.strip()
                if target and target != ws:
                    if workstream_store.rename(ws, target):
                        # Cascade the rename onto existing tasks.
                        moved = 0
                        for t in list_tasks(workstream=ws):
                            update(t.id, workstream=target)
                            moved += 1
                        st.success(f'Renamed to "{target}" ({moved} task(s) updated).')
                        st.rerun()
                    else:
                        st.warning("Rename failed — name blank, reserved, or a duplicate.")
            if c3.button("Delete", key=f"ws_delete_{ws}"):
                if workstream_store.delete(ws):
                    # Reassign affected tasks so none are orphaned.
                    moved = 0
                    for t in list_tasks(workstream=ws):
                        update(t.id, workstream="unassigned")
                        moved += 1
                    st.success(f'Deleted "{ws}" ({moved} task(s) → unassigned).')
                    st.rerun()


def render_ingestion_stubs() -> None:
    """Phase 2/3 ingestion entry points — shown as disabled stubs for now."""
    c1, c2 = st.columns(2)
    c1.button(
        "🎥 Scan Meet notes",
        disabled=True,
        help="Coming in Phase 2 — will scan Google Drive for recent Meet notes.",
        key="stub_meet",
    )
    c2.button(
        "📁 Import transcripts",
        disabled=True,
        help="Coming in Phase 3 — import uploaded Meet/Teams transcripts.",
        key="stub_transcripts",
    )


def render_kanban(tasks: list["Task"], group_by: str) -> None:
    """Render all swimlanes.  group_by is 'assignee' or 'workstream'."""
    tag_field = "workstream" if group_by == "assignee" else "assignee"
    groups = group_tasks_by(tasks, group_by)

    if group_by == "assignee":
        # Hanna: always-expanded, full-width primary board.
        hanna_tasks = groups.pop("Hanna", [])
        st.markdown("#### Hanna")
        render_swimlane("Hanna", hanna_tasks, tag_field, is_teammate=False)

        # Teammates: collapsible expanders with task-count badges.
        if TEAMMATE_ORDER:
            st.divider()
            for name in TEAMMATE_ORDER:
                lane_tasks = groups.get(name, [])
                n = len(lane_tasks)
                label = f"{name}  ({n} task{'s' if n != 1 else ''})"
                with st.expander(label, expanded=False):
                    render_swimlane(name, lane_tasks, tag_field, is_teammate=True)

        # Anything remaining (unassigned, etc.)
        for name, lane_tasks in sorted(groups.items()):
            if name not in TEAMMATE_ORDER:
                n = len(lane_tasks)
                with st.expander(f"{name}  ({n})", expanded=False):
                    render_swimlane(name, lane_tasks, tag_field, is_teammate=False)

    else:
        # By Workstream: one expander per workstream (expanded by default),
        # plus an "unassigned" lane at the end.
        _ws_list = get_workstreams()
        ordered_ws = _ws_list + (
            ["unassigned"] if "unassigned" not in _ws_list else []
        )
        rendered = set()
        for ws in ordered_ws:
            lane_tasks = groups.get(ws, [])
            n = len(lane_tasks)
            label = f"{ws}  ({n} task{'s' if n != 1 else ''})"
            with st.expander(label, expanded=(ws != "unassigned")):
                render_swimlane(ws, lane_tasks, tag_field, is_teammate=False)
            rendered.add(ws)

        # Any workstream not in the configured list (shouldn't happen, but safe).
        for ws, lane_tasks in sorted(groups.items()):
            if ws not in rendered:
                with st.expander(f"{ws}  ({len(lane_tasks)})", expanded=False):
                    render_swimlane(ws, lane_tasks, tag_field, is_teammate=False)


def render_board_tab() -> None:
    """Full board tab — view toggle, filters, add form, kanban."""
    if not _BOARD_AVAILABLE:
        st.warning(
            "Board not yet available — `task_store` module is not installed. "
            "Merge the foundation branch first, then restart the app."
        )
        return

    # ---- view toggle ----
    view = st.radio(
        "View",
        ["By Person", "By Workstream"],
        horizontal=True,
        key="board_view",
    )
    group_by = "assignee" if view == "By Person" else "workstream"

    # ---- filters ----
    filters = render_filter_bar()

    # ---- ingestion stubs (Phase 2/3) + add-form + workstream management ----
    render_ingestion_stubs()
    render_add_task_form()
    render_manage_workstreams()

    st.divider()

    # ---- load + filter tasks ----
    tasks = list_tasks()
    tasks = apply_filters(tasks, filters)

    if not tasks:
        st.info(
            "No tasks yet. "
            "Add one above or click **📋 Add to board** on an action-required "
            "thread in the Inbox Triage tab."
        )
        return

    render_kanban(tasks, group_by)


# ===========================================================================
# TRIAGE TAB  (existing behaviour, moved from main())
# ===========================================================================

def render_triage_tab(max_results: int | None, query: str) -> None:
    """Original 3-bucket inbox triage view."""
    st.caption(
        "Claude reads each thread and sorts your inbox. "
        "The only action here is **mark as read**. "
        "Action-required threads also show a **📋 Add to board** button."
    )

    if st.button("🔄 Fetch & classify", type="primary", key="triage_fetch"):
        try:
            fetch_and_classify(max_results, query)
        except AuthError as exc:
            st.error(str(exc))

    rows = st.session_state.get("rows", [])
    if not rows:
        st.caption("_No threads loaded yet. Click Fetch & classify above._")
        return

    # Assign stable sel_keys once rows are known.
    cats_seen: dict[str, int] = {}
    for r in rows:
        cat = r["decision"].category
        idx = cats_seen.get(cat, 0)
        cats_seen[cat] = idx + 1
        r["sel_key"] = f"sel_{cat}_{idx}"

    n_sel = sum(1 for r in rows if st.session_state.get(r["sel_key"], r["selected"]))
    label = (f"✅ Mark {n_sel} selected as read" if n_sel
             else "✅ Mark as read (select threads below)")
    st.button(
        label, type="primary", disabled=n_sel == 0,
        key="mark_read_top", on_click=_do_mark_read, args=(rows,),
    )

    st.divider()
    for cat, title, help_text in SECTIONS:
        render_section(cat, title, help_text, rows)
        st.divider()

    n_sel = sum(1 for r in rows if st.session_state.get(r["sel_key"], r["selected"]))
    label = (f"✅ Mark {n_sel} selected as read" if n_sel
             else "✅ Mark as read (select threads above)")
    st.button(
        label, type="primary", disabled=n_sel == 0,
        key="mark_read_bottom", on_click=_do_mark_read, args=(rows,),
    )


# ===========================================================================
# ENTRY POINT
# ===========================================================================

def main() -> None:
    st.title("📋 Gmail Action Board")
    max_results, query = sidebar()

    tab_board, tab_triage = st.tabs(["📋 Board", "📧 Inbox Triage"])
    with tab_board:
        render_board_tab()
    with tab_triage:
        render_triage_tab(max_results, query)


if __name__ == "__main__":
    main()
