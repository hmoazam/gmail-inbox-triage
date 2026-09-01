"""Classify inbox threads using Claude, and extract action items from transcripts.

Each thread is evaluated as a whole conversation: Claude reads the full
transcript (every message, full bodies) and decides, from the point of view of
the account owner (set via CLEANUP_USER_NAME), whether the thread needs a response,
whether it carries a concrete action on the owner, whether it's customer-related
or purely internal, and which UI bucket it belongs in.

Additionally exposes:
  - extract_actions_from_transcript(text, roster, workstreams, settings) → list[dict]
    Extracts action items from a meeting transcript or notes.
  - thread_decision_to_task(decision, thread, settings) → Task | None
    Bridge: converts an action_required ThreadDecision into a Task for the board.

Backends (see config.py):
  - "claude_cli"    : Claude Agent SDK (Claude Code SDK) driving the local
                      `claude` CLI. Default. Rich per-thread reasoning, no key.
  - "databricks_fm" : Databricks FM serving endpoint (fallback).
  - "anthropic_api" : direct Anthropic API (fallback; needs ANTHROPIC_API_KEY).

Safety: any thread we can't classify defaults to category "other" with no action.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re

from models import EmailThread, ThreadDecision, CATEGORIES, CATEGORY_OTHER

log = logging.getLogger(__name__)


def _system_prompt(settings: dict) -> str:
    name = settings["user_name"] or settings.get("user_email") or "the account owner"
    domain = settings["internal_domain"]
    return f"""You are an inbox triage assistant for {name}. You are given ONE \
email conversation (a full thread, every message, in order). Messages sent by \
{name} are marked "From: ME (account owner)". Analyze the conversation and \
return a single JSON object describing it.

Decide these fields:

- "customer_related": true if the thread involves an external customer, prospect, \
partner, or their use case / deal / support. false if it does not.
- "internal_only": true if every human participant is internal (addresses on \
@{domain}) and no external customer is involved. A thread can be internal_only \
and still be customer_related (e.g. internal discussion ABOUT a customer).
- "needs_response": true if the LATEST state of the thread is waiting on a reply \
from someone. Consider who sent the last message and what it asked.
- "action_on_me": if there is a concrete action, decision, or reply required \
specifically from {name}, write it as one short imperative sentence (e.g. \
"Reply to Alice with the Q3 architecture doc" or "Approve the budget request by \
Friday"). If {name} has already handled it, or the action is on someone else, or \
there is no action, set this to null. Base this on the WHOLE thread — if {name} \
already sent the needed reply, there is no outstanding action.
- "category": exactly one of:
    - "action_required": there IS an outstanding action on {name} (action_on_me \
is not null, typically needs_response). These go to the top of the inbox.
    - "useful": no action on {name}, but worth being aware of — product updates, \
announcements, or customer threads that have already been actioned/FYI.
    - "other": everything else — bulk mail, noise, notifications with no value.
- "summary": one short sentence. For action_required, describe the situation; \
for useful, say why it's worth knowing; for other, why it's noise.
- "confidence": 0.0-1.0.

Rules:
- Be precise about action_on_me: only flag a real action on {name}, not on others.
- If action_on_me is null, category MUST be "useful" or "other" (never action_required).
- If action_on_me is non-null, category MUST be "action_required".

Return ONLY the JSON object, no surrounding text:
{{"customer_related": bool, "internal_only": bool, "needs_response": bool, \
"action_on_me": "..." or null, "category": "action_required|useful|other", \
"summary": "...", "confidence": 0.0}}"""


def _user_prompt(thread: EmailThread, settings: dict) -> str:
    return ("Here is the full email thread. Classify it and return the JSON "
            "object only.\n\n" + thread.transcript(settings["per_msg_body_chars"]))


def _extract_json_object(text: str) -> dict:
    """Pull the first top-level JSON object out of the model's reply."""
    try:
        val = json.loads(text.strip())
        if isinstance(val, dict):
            return val
    except Exception:
        pass
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except Exception:
            pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            pass
    return {}


def _coerce(raw: dict, thread: EmailThread) -> ThreadDecision:
    """Map a raw dict to a ThreadDecision, enforcing invariants safely."""
    action = raw.get("action_on_me")
    if isinstance(action, str) and action.strip().lower() in ("", "null", "none"):
        action = None
    if action is not None:
        action = str(action).strip() or None

    category = str(raw.get("category", CATEGORY_OTHER)).lower()
    if category not in CATEGORIES:
        category = CATEGORY_OTHER

    # Enforce the action<->category invariant regardless of model slips.
    if action:
        category = "action_required"
    elif category == "action_required":
        # claimed action_required but gave no action -> demote to useful
        category = "useful"

    return ThreadDecision(
        thread_id=thread.thread_id,
        category=category,
        customer_related=bool(raw.get("customer_related", False)),
        internal_only=bool(raw.get("internal_only", False)),
        needs_response=bool(raw.get("needs_response", False)),
        action_on_me=action,
        summary=str(raw.get("summary", "")).strip(),
        confidence=float(raw.get("confidence", 0.0) or 0.0),
    )


def _safe_default(thread: EmailThread, reason: str) -> ThreadDecision:
    return ThreadDecision(
        thread_id=thread.thread_id, category=CATEGORY_OTHER,
        summary=reason, confidence=0.0,
    )


# --- usage stats ------------------------------------------------------------

class UsageStats:
    """Accumulates token counts and cost across a classification run."""
    def __init__(self) -> None:
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.cost_usd: float | None = None   # None when backend doesn't report USD
        self.claude_calls: int = 0           # threads that went to Claude (not quick-triage)

    def add(self, input_tok: int, output_tok: int, cost: float | None) -> None:
        self.input_tokens += input_tok
        self.output_tokens += output_tok
        if cost is not None:
            self.cost_usd = (self.cost_usd or 0.0) + cost
        self.claude_calls += 1

    def as_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "cost_usd": self.cost_usd,
            "claude_calls": self.claude_calls,
        }


# --- backends (return text + usage) ----------------------------------------

async def _via_cli(system: str, user: str, model: str) -> tuple[str, int, int, float | None]:
    from claude_agent_sdk import query, ClaudeAgentOptions
    from claude_agent_sdk.types import AssistantMessage, TextBlock, ResultMessage

    # The claude_cli backend authenticates through the user's claude.ai login,
    # not an API key. If ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN are present in
    # the environment (commonly exported from a shell profile), the spawned
    # `claude` CLI uses them instead of the login and prints:
    #   "claude.ai connectors are disabled because ANTHROPIC_API_KEY ... is set"
    # The SDK builds the child env as {**os.environ, **options.env}, a merge that
    # can't *unset* an inherited key — so we drop them from os.environ here.
    for _auth_var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        os.environ.pop(_auth_var, None)

    opts: dict = {"system_prompt": system, "max_turns": 1, "allowed_tools": []}
    if model:
        opts["model"] = model
    options = ClaudeAgentOptions(**opts)

    chunks: list[str] = []
    input_tok = output_tok = 0
    cost: float | None = None
    async for message in query(prompt=user, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    chunks.append(block.text)
            if message.usage:
                input_tok  = message.usage.get("input_tokens", 0)
                output_tok = message.usage.get("output_tokens", 0)
        elif isinstance(message, ResultMessage):
            cost = message.total_cost_usd
            if message.usage:
                input_tok  = message.usage.get("input_tokens", input_tok)
                output_tok = message.usage.get("output_tokens", output_tok)
    return "".join(chunks), input_tok, output_tok, cost


def _via_databricks_fm(system: str, user: str, endpoint: str,
                        profile: str) -> tuple[str, int, int, float | None]:
    from databricks.sdk import WorkspaceClient

    w = WorkspaceClient(profile=profile)
    client = w.serving_endpoints.get_open_ai_client()
    resp = client.chat.completions.create(
        model=endpoint or "databricks-claude-opus-4-8",
        max_tokens=1024,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
    )
    usage = resp.usage or {}
    return (
        resp.choices[0].message.content or "",
        getattr(usage, "prompt_tokens", 0) or 0,
        getattr(usage, "completion_tokens", 0) or 0,
        None,  # Databricks bills in DBUs, no USD figure here
    )


def _via_anthropic(system: str, user: str, model: str,
                   api_key: str) -> tuple[str, int, int, float | None]:
    from anthropic import Anthropic

    client = Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=model or "claude-opus-4-8", max_tokens=1024, system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    return text, resp.usage.input_tokens, resp.usage.output_tokens, None


def classify_thread(thread: EmailThread, settings: dict,
                    stats: "UsageStats | None" = None) -> ThreadDecision:
    """Classify a single thread. Never raises — returns a safe default on error."""
    system = _system_prompt(settings)
    user = _user_prompt(thread, settings)
    backend, model = settings["backend"], settings["model"]
    try:
        if backend == "databricks_fm":
            text, in_tok, out_tok, cost = _via_databricks_fm(
                system, user, model, settings["databricks_profile"])
        elif backend == "anthropic_api":
            key = settings.get("anthropic_api_key")
            if not key:
                raise RuntimeError("CLEANUP_BACKEND=anthropic_api but ANTHROPIC_API_KEY is not set.")
            text, in_tok, out_tok, cost = _via_anthropic(system, user, model, key)
        else:  # claude_cli (default)
            text, in_tok, out_tok, cost = asyncio.run(_via_cli(system, user, model))
    except Exception as exc:  # noqa: BLE001
        # Propagate auth-like errors so the caller can surface them to the user
        # rather than silently filing everything as "other".
        msg = str(exc)
        if any(k in type(exc).__name__ + msg for k in
               ("Auth", "401", "403", "Permission", "Credential", "Unauthorized")):
            raise
        return _safe_default(thread, f"Classification unavailable ({type(exc).__name__}).")

    if stats is not None:
        stats.add(in_tok, out_tok, cost)

    raw = _extract_json_object(text)
    if not raw:
        decision = _safe_default(thread, "Could not parse classification; treated as other.")
    else:
        decision = _coerce(raw, thread)

    return decision


def classify_threads(threads: list[EmailThread], settings: dict,
                     progress=None) -> tuple[list[ThreadDecision], UsageStats]:
    """Classify threads: rule-based triage, then cached decisions, then Claude.

    Every thread in `threads` gets a decision — nothing is dropped or hidden
    here (that's the caller's concern). This function only decides HOW a
    decision is obtained:
      1. quick_triage()   — instant, rule-based (subject/sender)
      2. decision_cache   — reuse a prior Claude classification if the thread's
                             content (message ids) hasn't changed since then
      3. Claude           — full evaluation, then cached for next time

    progress(done, total, instant_count, claude_count) is called after each
    thread, where instant_count = quick-triaged + cache hits.
    Returns (decisions, usage_stats).
    """
    from quick_triage import quick_triage
    import decision_cache

    stats = UsageStats()
    decisions: dict[str, ThreadDecision] = {}
    needs_claude: list[EmailThread] = []
    cache_hits: list[EmailThread] = []

    # Pass 1: instant rule-based triage (no network, no cache lookup needed).
    for th in threads:
        d = quick_triage(th)
        if d is not None:
            decisions[th.thread_id] = d
        else:
            cached = decision_cache.get(th)
            if cached is not None:
                decisions[th.thread_id] = cached
                cache_hits.append(th)
            else:
                needs_claude.append(th)

    instant_count = len(decisions)

    if progress:
        progress(instant_count, len(threads), instant_count, 0)

    # Pass 2: Claude SDK evaluation for threads that need it, then cache result.
    for i, th in enumerate(needs_claude, 1):
        decision = classify_thread(th, settings, stats=stats)
        decisions[th.thread_id] = decision
        decision_cache.put(th, decision)
        done = instant_count + i
        if progress:
            progress(done, len(threads), instant_count, i)

    # Return in original order.
    return [decisions[th.thread_id] for th in threads], stats


# ── transcript action extraction ─────────────────────────────────────────────

def _extract_json_array(text: str) -> list:
    """Pull the first top-level JSON array out of the model's reply.

    Mirrors _extract_json_object but expects a `[...]` root rather than `{...}`.
    Returns [] on any parse failure so callers always get a list.
    """
    try:
        val = json.loads(text.strip())
        if isinstance(val, list):
            return val
    except Exception:
        pass

    # Fenced code block
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if fenced:
        try:
            val = json.loads(fenced.group(1))
            if isinstance(val, list):
                return val
        except Exception:
            pass

    # Bare array anywhere in the text
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            val = json.loads(text[start:end + 1])
            if isinstance(val, list):
                return val
        except Exception:
            pass

    return []


def _action_system_prompt(name: str, roster: list[str], workstreams: list[str]) -> str:
    roster_str = ", ".join(roster) if roster else "unknown"
    ws_str = ", ".join(workstreams) if workstreams else "unassigned"
    return (
        f"You are an action-item extractor for {name}. "
        "Given a meeting transcript or notes, identify every concrete commitment, "
        "decision, or task that requires follow-up by someone on the team. "
        "For each item, determine:\n"
        f"- \"assignee\": match the responsible person's name against the roster "
        f"({roster_str}), or use \"unknown\" if unclear.\n"
        f"- \"workstream\": suggest the best matching workstream from "
        f"({ws_str}), or use \"unassigned\" if it doesn't fit.\n"
        "- \"action\": state the task as one concise imperative sentence.\n"
        "- \"due_date\": ISO date string (YYYY-MM-DD) if a deadline is mentioned, "
        "otherwise null.\n"
        "- \"context\": 1–2 sentences quoted or paraphrased from the transcript "
        "that support the action item.\n\n"
        "Rules:\n"
        "- Only include items with a real follow-up obligation — not general "
        "discussion points.\n"
        "- If no action items exist, return an empty array.\n"
        "- Ambiguous assignees land in \"unknown\" rather than being guessed.\n"
        "- Ambiguous workstreams land in \"unassigned\" rather than being guessed.\n\n"
        "Return ONLY a JSON array; no surrounding text, no markdown prose:\n"
        '[{"assignee":"...","workstream":"...","action":"...","due_date":null,'
        '"context":"..."}, ...]'
    )


def _action_user_prompt(text: str, per_msg_body_chars: int) -> str:
    # Allow larger transcripts than individual email bodies (5x per-msg cap).
    cap = per_msg_body_chars * 5
    snippet = text[:cap]
    if len(text) > cap:
        snippet += f"\n\n[transcript truncated at {cap} chars]"
    return (
        "Extract all action items from the following transcript. "
        "Return the JSON array only.\n\n"
        + snippet
    )


def extract_actions_from_transcript(
    text: str,
    roster: list[str],
    workstreams: list[str],
    settings: dict,
) -> list[dict]:
    """Extract action items from a meeting transcript or notes.

    Args:
        text:        Raw transcript text (Meet auto-notes, Teams export, .txt, etc.).
        roster:      List of team member names for assignee matching.
                     Derive from config via [r["name"] for r in TEAM_ROSTER].
        workstreams: List of known workstream names for classification.
        settings:    The app settings dict from config.get_settings().

    Returns a list of dicts, each with keys:
        assignee   — matched roster name or "unknown"
        workstream — matched workstream or "unassigned"
        action     — imperative sentence describing the task
        due_date   — "YYYY-MM-DD" string or null
        context    — 1–2 sentence excerpt providing context

    Never raises — returns [] on any error (safe default).
    Auth-like errors are re-raised so the caller can surface them to the user.
    """
    if not text or not text.strip():
        return []

    name = settings.get("user_name") or settings.get("user_email") or "the account owner"
    system = _action_system_prompt(name, roster, workstreams)
    user = _action_user_prompt(text, settings.get("per_msg_body_chars", 6000))
    backend = settings.get("backend", "claude_cli")
    model = settings.get("model", "")

    try:
        if backend == "databricks_fm":
            raw_text, _, _, _ = _via_databricks_fm(
                system, user, model, settings["databricks_profile"]
            )
        elif backend == "anthropic_api":
            key = settings.get("anthropic_api_key")
            if not key:
                raise RuntimeError(
                    "CLEANUP_BACKEND=anthropic_api but ANTHROPIC_API_KEY is not set."
                )
            raw_text, _, _, _ = _via_anthropic(system, user, model, key)
        else:  # claude_cli (default)
            raw_text, _, _, _ = asyncio.run(_via_cli(system, user, model))
    except Exception as exc:
        msg = str(exc)
        if any(k in type(exc).__name__ + msg for k in
               ("Auth", "401", "403", "Permission", "Credential", "Unauthorized")):
            raise
        log.warning(
            "extract_actions_from_transcript: backend error (%s: %s) — returning [].",
            type(exc).__name__, msg,
        )
        return []

    items = _extract_json_array(raw_text)
    if not items:
        log.warning(
            "extract_actions_from_transcript: could not parse action array from "
            "model reply — returning []."
        )
        return []

    # Normalise each item: enforce required keys and safe defaults.
    cleaned: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        action_text = str(item.get("action") or "").strip()
        if not action_text:
            continue  # Skip items without a real action text
        cleaned.append({
            "assignee":   str(item.get("assignee") or "unknown").strip() or "unknown",
            "workstream": str(item.get("workstream") or "unassigned").strip() or "unassigned",
            "action":     action_text,
            "due_date":   item.get("due_date"),   # None or "YYYY-MM-DD" string
            "context":    str(item.get("context") or "").strip(),
        })
    return cleaned


# ── email → Task bridge ───────────────────────────────────────────────────────

def thread_decision_to_task(
    decision: "ThreadDecision",
    thread: "EmailThread",
    settings: dict,
) -> "Task | None":
    """Convert an action_required ThreadDecision into a Task for the board.

    Returns None if the decision is not action_required or has no action_on_me.
    Does NOT persist the task — the caller is responsible for calling
    task_store.add(task) after dedup-checking via task_store.find_by_source().

    The Task is built via task_store.new_task() so it gets a UUID + timestamps.
    Assignee is always the account owner (Hanna), since action_on_me means it's
    her action. Workstream defaults to "unassigned" for manual classification on
    the board.
    """
    if decision.category != "action_required" or not decision.action_on_me:
        return None

    from task_store import new_task  # deferred import to avoid circular at module load

    assignee = (
        settings.get("user_name")
        or settings.get("user_email")
        or "Hanna"
    )
    thread_id = decision.thread_id
    source_link = f"https://mail.google.com/mail/u/0/#inbox/{thread_id}"

    return new_task(
        title=decision.action_on_me,
        assignee=assignee,
        workstream="unassigned",
        status="todo",
        source="email",
        source_ref=thread_id,
        source_link=source_link,
        context=decision.summary or "",
        customer_related=decision.customer_related,
        confidence=decision.confidence,
    )
