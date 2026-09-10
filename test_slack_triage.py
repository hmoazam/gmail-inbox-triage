"""Backend verification for the Slack triage feature — no live Slack.

Uses FastAPI TestClient with a MOCKED slack_client (canned conversations) and a
stubbed classifier, asserting:
  - group assembly + ordering (Direct Messages first, then configured categories)
  - only-unread conversations surface, and every group is emitted
  - SlackConversationOut / decision JSON shape matches the contract
  - mark-read
  - add-to-board create → dedup
  - AuthError → 401/403
"""
from __future__ import annotations

import importlib
import tempfile

import pytest
from fastapi.testclient import TestClient

import config
from gmail_client import AuthError
from slack_client import SlackConversation, SlackMessage
from models import ThreadDecision
import task_store


# --- fixtures ---------------------------------------------------------------

@pytest.fixture()
def isolated_store(monkeypatch, tmp_path):
    """Point task_store at a temp file so add-to-board dedup is deterministic."""
    monkeypatch.setattr(task_store, "STORE_PATH", tmp_path / "tasks.json")
    yield


@pytest.fixture()
def client(monkeypatch, isolated_store):
    from api import deps
    from api.routes import slack_triage
    import classifier

    # Small, ordered category map so we can assert ordering precisely.
    cats = {"AI Gateway Accounts": [], "Team": ["C_TEAM"], "Rolls Royce": [], "SME": []}
    settings = {
        **config.get_settings(),
        "slack_categories": cats,
        "user_name": "Hanna",
        "backend": "claude_cli",
        "model": "",
        "per_msg_body_chars": 6000,
    }
    monkeypatch.setattr(deps, "get_app_settings", lambda: settings)

    # --- mocked slack client -------------------------------------------------
    dm = SlackConversation(
        id="D1", kind="im", name="Alice Example",
        messages=[SlackMessage(author="Alice Example", ts="1.1", text="ping?")],
        latest_ts="1.1", permalink="https://slack.example/D1",
    )
    ch = SlackConversation(
        id="C_TEAM", kind="channel", name="team-standup",
        messages=[SlackMessage(author="Bob", ts="2.1", text="deploy done")],
        latest_ts="2.1", permalink="https://slack.example/C_TEAM",
    )

    class FakeSlack:
        def list_unread_dms(self):
            return [dm]

        def list_unread_in_channels(self, channel_ids):
            return [ch] if "C_TEAM" in channel_ids else []

        def mark_read(self, channel_id, ts):
            self.marked = (channel_id, ts)
            return None

    fake = FakeSlack()
    monkeypatch.setattr(deps, "get_slack_client", lambda: fake)
    monkeypatch.setattr(slack_triage, "get_slack_client", lambda: fake)
    monkeypatch.setattr(slack_triage, "get_app_settings", lambda: settings)

    # --- stubbed classifier --------------------------------------------------
    def fake_classify(conv, settings, stats=None):
        if stats is not None:
            stats.add(10, 5, None)
        return ThreadDecision(
            thread_id=conv.id, category="action_required",
            customer_related=True, internal_only=False, needs_response=True,
            action_on_me=f"Reply in {conv.name}", summary=f"summary for {conv.name}",
            confidence=0.9,
        )

    monkeypatch.setattr(slack_triage, "classify_slack_conversation", fake_classify)

    from api.main import app
    return TestClient(app), fake


# --- tests ------------------------------------------------------------------

def test_group_assembly_and_ordering(client):
    tc, _ = client
    r = tc.get("/api/slack-triage")
    assert r.status_code == 200
    body = r.json()

    # Groups present in the fixed order, DMs first.
    assert [g["category"] for g in body["groups"]] == [
        "Direct Messages", "AI Gateway Accounts", "Team", "Rolls Royce", "SME",
    ]

    dm_group = body["groups"][0]
    assert len(dm_group["conversations"]) == 1
    conv = dm_group["conversations"][0]

    # SlackConversationOut shape.
    assert set(conv.keys()) == {
        "id", "kind", "name", "unread_count", "messages",
        "latest_ts", "permalink", "decision",
    }
    assert conv["id"] == "D1"
    assert conv["kind"] == "im"
    assert conv["unread_count"] == 1
    assert conv["messages"] == [{"author": "Alice Example", "ts": "1.1", "text": "ping?"}]
    assert conv["latest_ts"] == "1.1"
    assert conv["permalink"] == "https://slack.example/D1"

    # Nested decision shape — exactly the 7 contract fields (no thread_id).
    assert set(conv["decision"].keys()) == {
        "category", "summary", "action_on_me", "customer_related",
        "internal_only", "needs_response", "confidence",
    }
    assert conv["decision"]["category"] == "action_required"
    assert conv["decision"]["action_on_me"] == "Reply in Alice Example"

    # Team group has the channel; empty categories are still emitted, empty.
    team = next(g for g in body["groups"] if g["category"] == "Team")
    assert [c["id"] for c in team["conversations"]] == ["C_TEAM"]
    empties = [g for g in body["groups"] if g["category"] in ("AI Gateway Accounts", "Rolls Royce", "SME")]
    assert all(g["conversations"] == [] for g in empties)

    # Usage accumulated across the two classified conversations.
    assert body["usage"]["claude_calls"] == 2
    assert body["usage"]["input_tokens"] == 20


def test_mark_read(client):
    tc, fake = client
    r = tc.post("/api/slack-triage/mark-read", json={"channel_id": "C_TEAM", "ts": "2.1"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert fake.marked == ("C_TEAM", "2.1")


def test_add_to_board_create_then_dedup(client):
    tc, _ = client
    payload = {
        "channel_id": "C_TEAM", "name": "team-standup",
        "permalink": "https://slack.example/C_TEAM",
        "summary": "deploy discussion", "action_on_me": "Reply to Bob about deploy",
        "customer_related": False, "needs_response": True, "confidence": 0.8,
    }
    r1 = tc.post("/api/slack-triage/add-to-board", json=payload)
    assert r1.status_code == 200
    b1 = r1.json()
    assert b1["created"] is True
    assert b1["task"]["source"] == "slack"
    assert b1["task"]["source_ref"] == "C_TEAM"
    assert b1["task"]["title"] == "Reply to Bob about deploy"
    assert b1["task"]["assignee"] == "Hanna"        # default owner

    # Second identical call → dedup hit, same task, created=False.
    r2 = tc.post("/api/slack-triage/add-to-board", json=payload)
    assert r2.status_code == 200
    b2 = r2.json()
    assert b2["created"] is False
    assert b2["task"]["id"] == b1["task"]["id"]


def test_add_to_board_title_falls_back_to_summary(client):
    tc, _ = client
    payload = {
        "channel_id": "D9", "name": "Zed", "permalink": None,
        "summary": "FYI: launch is live", "action_on_me": None,
        "customer_related": False, "needs_response": False, "confidence": 0.5,
    }
    r = tc.post("/api/slack-triage/add-to-board", json=payload)
    assert r.status_code == 200
    assert r.json()["task"]["title"] == "FYI: launch is live"


def test_auth_error_maps_to_401(client, monkeypatch):
    tc, fake = client

    def boom():
        raise AuthError("Slack API returned 401. Re-export SLACK_USER_TOKEN.")

    monkeypatch.setattr(fake, "list_unread_dms", boom)
    r = tc.get("/api/slack-triage")
    assert r.status_code == 401
    assert "SLACK_USER_TOKEN" in r.json()["detail"]


def test_auth_error_403_from_classifier(client, monkeypatch):
    tc, _ = client
    from api.routes import slack_triage

    def boom(conv, settings, stats=None):
        raise AuthError("Slack API returned 403 (missing_scope).")

    monkeypatch.setattr(slack_triage, "classify_slack_conversation", boom)
    r = tc.get("/api/slack-triage")
    assert r.status_code == 403


def test_mark_read_auth_error_403(client, monkeypatch):
    tc, fake = client

    def boom(channel_id, ts):
        raise AuthError("Slack API returned 403. token_expired.")

    monkeypatch.setattr(fake, "mark_read", boom)
    r = tc.post("/api/slack-triage/mark-read", json={"channel_id": "C", "ts": "1"})
    assert r.status_code == 403
