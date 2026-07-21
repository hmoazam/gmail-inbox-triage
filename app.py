"""Gmail inbox triage — local Streamlit app.

Reads your inbox as conversations, has Claude (via the Claude Code SDK) read each
full thread and sort it into three buckets:
  1. Action Required — a concrete action on you (shown with the action text)
  2. Useful          — no action, but worth being aware of
  3. Other           — everything else

The ONLY action you can take from this UI is "Mark selected as read". Nothing is
moved, archived, labelled, or deleted — ever.

Run:  streamlit run app.py
Auth: `gcloud auth application-default login` (Gmail) before use.
      Claude Code SDK uses your already-authenticated local `claude` CLI.
"""
from __future__ import annotations

import os

import streamlit as st

import mlflow_tracking
mlflow_tracking.configure()  # sets tracking URI + disables async logging

from config import get_settings
from gmail_client import GmailClient, AuthError
from classifier import classify_threads
import decision_cache
from models import (
    CATEGORY_ACTION, CATEGORY_USEFUL, CATEGORY_OTHER, EmailThread, ThreadDecision,
)

st.set_page_config(page_title="Gmail Inbox Triage", page_icon="📧", layout="wide")

SECTIONS = [
    (CATEGORY_ACTION, "🚨 Action Required", "Threads with a concrete action on you."),
    (CATEGORY_USEFUL, "💡 Useful", "No action needed, but worth being aware of."),
    (CATEGORY_OTHER,  "🗂️ Other", "Everything else."),
]


@st.cache_resource
def get_client() -> GmailClient:
    return GmailClient()


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

    with mlflow_tracking.triage_run(
        query=query,
        total_threads=len(threads),
        backend=settings["backend"],
        model=settings["model"] or "cli_default",
    ) as run_id:
        try:
            decisions, stats = classify_threads(threads, settings, progress=on_class)
        except Exception as exc:  # noqa: BLE001 — auth errors re-raised from classify_thread
            class_bar.empty()
            st.error(
                f"Classification failed — likely a backend auth error.\n\n"
                f"**{type(exc).__name__}:** {exc}\n\n"
                f"For `claude_cli`: make sure the `claude` CLI is authenticated.\n"
                f"For `databricks_fm`: check your `{settings['databricks_profile']}` "
                f"profile is still valid."
            )
            return
        mlflow_tracking.log_run_summary(stats.as_dict(),
                                        quick_count=len(threads) - stats.claude_calls)

    class_bar.empty()

    st.session_state.last_usage = stats.as_dict()
    st.session_state.last_run_id = run_id  # may be None if MLflow unavailable
    st.session_state.last_run_url = mlflow_tracking.run_url(run_id)   # None-safe
    st.session_state.experiment_url = mlflow_tracking.experiment_url()  # None-safe
    by_id = {d.thread_id: d for d in decisions}
    # Every fetched thread is shown — caching only affects HOW the decision
    # was obtained (rule / cache / Claude), never whether it's displayed.
    st.session_state.rows = [
        {"thread": t, "decision": by_id[t.thread_id], "selected": False}
        for t in threads
    ]


def sidebar() -> tuple[int, str]:
    s = get_settings()
    st.sidebar.header("⚙️ Settings")
    st.sidebar.caption(f"User: **{s['user_name'] or s['user_email'] or 'auto-detected from Gmail'}**")
    st.sidebar.caption(f"Backend: **{s['backend']}**")
    st.sidebar.caption(f"Model: **{s['model'] or 'CLI default'}**")
    st.sidebar.caption(f"Internal domain: **{('@' + s['internal_domain']) if s['internal_domain'] else 'not set'}**")
    query = st.sidebar.text_input("Gmail search filter", value="in:inbox is:unread")
    st.sidebar.caption("e.g. `in:inbox is:unread`, `in:inbox newer_than:7d`")
    cap_on = st.sidebar.toggle("Cap results (for testing)", value=False)
    max_results: int | None = None
    if cap_on:
        max_results = st.sidebar.slider("Max threads", 5, 200, 30, step=5)
    st.sidebar.divider()
    st.sidebar.caption("🔒 The only action here is **mark as read**. "
                       "Nothing is moved, archived, or deleted.")
    if not mlflow_tracking.is_available():
        reason = mlflow_tracking.unavailable_reason()
        st.sidebar.warning(f"⚠️ MLflow unavailable — triage still works but "
                           f"runs won't be logged.\n\n`{reason}`")
    n_cached = decision_cache.count()
    st.sidebar.caption(f"Classification cache: **{n_cached}** thread(s). "
                       "Unread threads with no new messages reuse their prior "
                       "result instead of calling Claude again.")
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
        run_url = st.session_state.get("last_run_url")
        exp_url = st.session_state.get("experiment_url")
        if run_url:
            st.sidebar.markdown(f"[📊 View this run in Databricks]({run_url})")
        if exp_url:
            st.sidebar.markdown(f"[🔬 All runs — experiment]({exp_url})")
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
    # Sync selection from session_state (checkboxes are the source of truth).
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
    # Mark as seen so they don't appear on the next run.
    # Reflect locally so the UI updates without a refetch. If your filter is
    # "is:unread" these threads won't reappear next fetch anyway; if it isn't,
    # they'll reappear (correctly) still showing their cached classification.
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


def main() -> None:
    st.title("📧 Gmail Inbox Triage")
    st.caption("Claude reads each thread and sorts your inbox. "
               "The only action here is **mark as read**.")
    max_results, query = sidebar()

    if st.button("🔄 Fetch & classify", type="primary"):
        try:
            fetch_and_classify(max_results, query)
        except AuthError as exc:
            st.error(str(exc))

    rows = st.session_state.get("rows", [])
    if not rows:
        return

    # Assign stable sel_keys once rows are known, so mark_selected_read can find them.
    cats_seen: dict[str, int] = {}
    for r in rows:
        cat = r["decision"].category
        idx = cats_seen.get(cat, 0)
        cats_seen[cat] = idx + 1
        r["sel_key"] = f"sel_{cat}_{idx}"

    # Count from session_state so the button label is always current.
    n_sel = sum(1 for r in rows if st.session_state.get(r["sel_key"], r["selected"]))
    label = f"✅ Mark {n_sel} selected as read" if n_sel else "✅ Mark as read (select threads below)"
    st.button(label, type="primary", disabled=n_sel == 0,
              key="mark_read_top", on_click=_do_mark_read, args=(rows,))

    st.divider()
    for cat, title, help_text in SECTIONS:
        render_section(cat, title, help_text, rows)
        st.divider()

    # Repeat the action button at the bottom so you don't have to scroll back up.
    n_sel = sum(1 for r in rows if st.session_state.get(r["sel_key"], r["selected"]))
    label = f"✅ Mark {n_sel} selected as read" if n_sel else "✅ Mark as read (select threads above)"
    st.button(label, type="primary", disabled=n_sel == 0,
              key="mark_read_bottom", on_click=_do_mark_read, args=(rows,))

    render_mlflow_panel()


def _do_mark_read(rows: list[dict]) -> None:
    mark_selected_read(rows)


def render_mlflow_panel() -> None:
    """Show recent triage runs from the MLflow experiment."""
    import mlflow
    from mlflow.tracking import MlflowClient

    with st.expander("📊 MLflow — triage run history", expanded=False):
        exp_url = st.session_state.get("experiment_url", "")
        if exp_url:
            st.markdown(f"[Open experiment in Databricks ↗]({exp_url})")
        try:
            client = MlflowClient()
            exp_name = os.environ.get("MLFLOW_EXPERIMENT", "")
            if not exp_name:
                st.caption("MLflow tracking not configured (set MLFLOW_EXPERIMENT).")
                return
            exp = mlflow.get_experiment_by_name(exp_name)
            if exp is None:
                st.caption("Experiment not found.")
                return
            runs = client.search_runs(
                experiment_ids=[exp.experiment_id],
                order_by=["start_time DESC"],
                max_results=10,
            )
            if not runs:
                st.caption("No runs logged yet.")
                return

            rows_data = []
            run_url_base = st.session_state.get("last_run_url", "")
            host = run_url_base.split("/ml/experiments")[0] if run_url_base else ""
            for r in runs:
                m = r.data.metrics
                p = r.data.params
                run_link = (f"{host}/ml/experiments/{exp.experiment_id}/runs/{r.info.run_id}"
                            if host else r.info.run_id[:8])
                rows_data.append({
                    "Run": f"[{r.info.run_name}]({run_link})" if host else r.info.run_name,
                    "Threads": int(p.get("total_threads", 0)),
                    "Claude calls": int(m.get("claude_calls", 0)),
                    "Quick-triaged": int(m.get("quick_triaged", 0)),
                    "Input tok": f"{int(m.get('input_tokens', 0)):,}",
                    "Output tok": f"{int(m.get('output_tokens', 0)):,}",
                    "Cost (USD)": (f"${m['cost_usd']:.4f}" if "cost_usd" in m else "—"),
                    "Query": p.get("query", ""),
                })
            st.dataframe(rows_data, use_container_width=True, hide_index=True)
        except Exception as exc:
            st.caption(f"Could not load run history: {exc}")


if __name__ == "__main__":
    main()
