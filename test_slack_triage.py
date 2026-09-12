"""Backend verification for URL-driven Slack action extraction — no live dbexec.

Covers:
  - parse_slack_url: channel URL, thread URL (p-ts → dotted ts), reject non-Slack
  - parse_extracted_actions: strip the [MCP_PRIVACY_SUMMARIZED] marker + ```json
    fence, parse the array, coerce items, drop empties; [] on garbage
  - POST /api/slack-triage/extract with a MOCKED slack_client.extract_actions:
    contract shape, bad URL → 422, timeout → 504, AuthError → 401
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gmail_client import AuthError
from slack_client import parse_slack_url, parse_extracted_actions


# --- parse_slack_url --------------------------------------------------------

def test_parse_channel_url():
    ch, ts = parse_slack_url("https://databricks.slack.com/archives/C0ADP69J1P0")
    assert ch == "C0ADP69J1P0"
    assert ts is None


def test_parse_thread_url_splits_ts():
    ch, ts = parse_slack_url(
        "https://databricks.slack.com/archives/C0ADP69J1P0/p1712345678123456")
    assert ch == "C0ADP69J1P0"
    assert ts == "1712345678.123456"


def test_parse_rejects_non_slack_url():
    with pytest.raises(ValueError):
        parse_slack_url("https://example.com/foo/bar")
    with pytest.raises(ValueError):
        parse_slack_url("")


# --- parse_extracted_actions ------------------------------------------------

def test_parse_marker_and_json_fence():
    raw = (
        "[MCP_PRIVACY_SUMMARIZED] I analyzed the conversation.\n\n"
        "```json\n"
        '[{"task": "Reply to Bob with the design doc", '
        '"context": "Bob asked in-thread", "due": "2026-09-20"},'
        '{"task": "Book the review slot", "context": "before Friday", "due": null}]'
        "\n```"
    )
    actions = parse_extracted_actions(raw)
    assert actions == [
        {"task": "Reply to Bob with the design doc",
         "context": "Bob asked in-thread", "due": "2026-09-20"},
        {"task": "Book the review slot", "context": "before Friday", "due": None},
    ]


def test_parse_bare_array_and_coercion():
    # No marker, no fence; non-string due and an empty-task item get coerced/dropped.
    raw = '[{"task": "Do X", "due": 123}, {"task": "  ", "context": "skip"}]'
    assert parse_extracted_actions(raw) == [{"task": "Do X", "context": "", "due": None}]


def test_parse_empty_and_garbage_return_empty():
    assert parse_extracted_actions("[MCP_PRIVACY_SUMMARIZED] none found\n\n[]") == []
    assert parse_extracted_actions("not json at all") == []
    assert parse_extracted_actions("") == []


# --- route: POST /api/slack-triage/extract ----------------------------------

@pytest.fixture()
def client(monkeypatch):
    from api import deps
    from api.routes import slack_triage

    settings = {"user_name": "Hanna", "user_email": "hanna@example.com"}
    monkeypatch.setattr(slack_triage, "get_app_settings", lambda: settings)

    class FakeSlack:
        def __init__(self):
            self.calls = []
            self.result = [{"task": "Reply to Bob", "context": "asked in-thread",
                            "due": "2026-09-20"}]
            self.error = None

        def extract_actions(self, url, user_name=None):
            self.calls.append((url, user_name))
            if self.error is not None:
                raise self.error
            return self.result

    fake = FakeSlack()
    monkeypatch.setattr(slack_triage, "get_slack_client", lambda: fake)

    from api.main import app
    return TestClient(app), fake


def test_extract_contract_shape(client):
    tc, fake = client
    url = "https://databricks.slack.com/archives/C0ADP69J1P0/p1712345678123456"
    r = tc.post("/api/slack-triage/extract", json={"url": url})
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"source_link", "actions"}
    assert body["source_link"] == url
    assert body["actions"] == [
        {"task": "Reply to Bob", "context": "asked in-thread", "due": "2026-09-20"}
    ]
    # The account owner is passed through to the extractor.
    assert fake.calls == [(url, "Hanna")]


def test_extract_empty_actions(client):
    tc, fake = client
    fake.result = []
    r = tc.post("/api/slack-triage/extract",
                json={"url": "https://x.slack.com/archives/C1"})
    assert r.status_code == 200
    assert r.json()["actions"] == []


def test_extract_bad_url_maps_to_422(client):
    tc, fake = client
    fake.error = ValueError("Not a Slack conversation URL: 'nope'.")
    r = tc.post("/api/slack-triage/extract", json={"url": "nope"})
    assert r.status_code == 422
    assert "Slack" in r.json()["detail"]


def test_extract_timeout_maps_to_504(client):
    tc, fake = client
    fake.error = TimeoutError("Slack MCP tool timed out after 180s.")
    r = tc.post("/api/slack-triage/extract",
                json={"url": "https://x.slack.com/archives/C1"})
    assert r.status_code == 504
    assert "timed out" in r.json()["detail"]


def test_extract_auth_error_maps_to_401(client):
    tc, fake = client
    fake.error = AuthError(
        "Slack via dbexec is unavailable — ensure dbexec is installed and authenticated.")
    r = tc.post("/api/slack-triage/extract",
                json={"url": "https://x.slack.com/archives/C1"})
    assert r.status_code == 401
    assert "dbexec" in r.json()["detail"]
