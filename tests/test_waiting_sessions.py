"""Tests for how the engine answers a Jules session that stops to ask a question.

WHY THIS EXISTS
---------------
ADR 0004. A Jules worker on Slackbot ticket 46 stopped mid-ticket to ask "does this
progress look correct?" even though the session was created with plan approval off
and the prompt says no human is watching. A session waiting in
`AWAITING_USER_FEEDBACK` counts as live, so its claim was never released and the
ticket sat overnight waiting for an answer nobody would give.

The engine now answers for the developer: it replies "proceed unattended" up to
`max_auto_replies` times, then escalates the ticket on its claim branch and tells the
session to stop. These tests drive the dispatch core from a snapshot and
`LiveDispatcher.dispatch` end to end, and assert what the engine does in the world:
which message reaches which session, what is committed where, and what the run
report tells the developer.

Faked: the GitHub and Jules clients (MagicMock), and the Jules HTTP layer for the
adapter contract tests. Real: the parser, the dispatch core, LiveDispatcher, the run
report, and the config loader.
"""
from __future__ import annotations

import datetime
import json
import pathlib
from unittest.mock import MagicMock, patch

from ticket_engine.config import RepoConfig, load_repo_config
from ticket_engine.dispatch import (
    AUTO_REPLY_MARKER,
    STOP_MARKER,
    AnswerSessionAction,
    DispatchCore,
    EscalatedSessionStillWaiting,
    EscalateWaitingSessionAction,
    StartTicketAction,
    WorldSnapshot,
)
from ticket_engine.jules import JulesClient
from ticket_engine.live_dispatch import LiveDispatcher
from ticket_engine.parser import Ticket, TicketParser
from ticket_engine.prompt import load_ticket_skill
from ticket_engine.run_report import build_run_report

REPO = "owner/Slackbot"
EFFORT = "purchase-path-and-epif"
NOW = datetime.datetime(2026, 9, 25, 22, 0, tzinfo=datetime.UTC)
SESSION = "sessions/9046"
TICKET_TEXT = (
    "# 46: EPIF-path buyer gets the filled EPIF in the DM\n\n"
    "**Status:** ready-for-agent\n\n"
    "**Blocked by:** None\n\n"
    "**What to build:** On the EPIF path, the buyer's DM carries the EPIF.\n\n"
    "- [ ] The DM carries the EPIF.\n\n"
    "## Comments\n"
)


def ticket(number: int = 46, status: str = "ready-for-agent", effort: str = EFFORT) -> Ticket:
    """Parse a real ticket body at a repo-relative path, as the dispatcher reads it."""
    text = TICKET_TEXT.replace("# 46:", f"# {number}:").replace("ready-for-agent", status)
    path = pathlib.Path(".scratch") / effort / "issues" / f"{number:02d}-epif-dm.md"
    return TicketParser().parse_text(text, filename=path.name, path=path)


def agent_asks(minute: int) -> dict:
    return {
        "originator": "agent",
        "createTime": f"2026-09-25T21:{minute:02d}:00Z",
        "agentMessaged": {"agentMessage": "Does this progress look correct to you?"},
    }


def engine_replied(minute: int) -> dict:
    return {
        "originator": "user",
        "createTime": f"2026-09-25T21:{minute:02d}:00Z",
        "userMessaged": {"userMessage": f"{AUTO_REPLY_MARKER} Proceed."},
    }


def engine_stopped(minute: int) -> dict:
    return {
        "originator": "user",
        "createTime": f"2026-09-25T21:{minute:02d}:00Z",
        "userMessaged": {"userMessage": f"{STOP_MARKER} Stop."},
    }


def waiting_session(
    activities: list[dict] | None,
    number: int = 46,
    state: str = "AWAITING_USER_FEEDBACK",
    repo: str = REPO,
    effort: str = EFFORT,
) -> dict:
    sess = {
        "name": SESSION,
        "id": "9046",
        "state": state,
        "title": f"{effort}-{number:02d}: EPIF-path buyer gets the filled EPIF in the DM",
        "sourceContext": {"source": f"sources/github/{repo}"},
        "createTime": "2026-09-25T20:00:00Z",
    }
    if activities is not None:
        sess["activities"] = activities
    return sess


def evaluate(sessions: list[dict], config: RepoConfig | None = None, tickets=None, paused=False):
    snap = WorldSnapshot(
        tickets=tickets if tickets is not None else [ticket()],
        claims={f"claim/{EFFORT}/46"},
        jules_sessions=sessions,
        config=config or RepoConfig(),
        repo_name=REPO,
        now=NOW,
        paused=paused,
    )
    return DispatchCore().evaluate(snap).actions


def of_type(actions, cls):
    return [a for a in actions if isinstance(a, cls)]


# --- dispatch core ------------------------------------------------------------


def test_first_question_gets_the_unattended_reply():
    actions = evaluate([waiting_session([agent_asks(1)])])
    answers = of_type(actions, AnswerSessionAction)
    assert len(answers) == 1
    a = answers[0]
    assert a.session_name == SESSION
    assert a.ticket_number == 46
    assert a.reply_number == 1
    assert a.message.startswith(AUTO_REPLY_MARKER)
    assert "No human is watching" in a.message
    assert "Status:` to `blocked`" in a.message
    assert not of_type(actions, EscalateWaitingSessionAction)


def test_session_that_asks_again_after_one_reply_gets_the_second_reply():
    actions = evaluate([waiting_session([agent_asks(1), engine_replied(2), agent_asks(3)])])
    answers = of_type(actions, AnswerSessionAction)
    assert [a.reply_number for a in answers] == [1 + 1]


def test_session_that_asks_again_after_the_last_allowed_reply_is_escalated():
    acts = [agent_asks(1), engine_replied(2), agent_asks(3), engine_replied(4), agent_asks(5)]
    actions = evaluate([waiting_session(acts)])

    assert not of_type(actions, AnswerSessionAction)
    (esc,) = of_type(actions, EscalateWaitingSessionAction)
    assert esc.session_name == SESSION
    assert esc.claim_ref == f"claim/{EFFORT}/46"
    assert esc.ticket_path == f".scratch/{EFFORT}/issues/46-epif-dm.md"
    assert esc.stop_message.startswith(STOP_MARKER)
    brief = esc.brief
    assert brief.startswith("## Escalation — 2026-09-25\n")
    assert f"Ticket: 46 EPIF-path buyer gets the filled EPIF in the DM   Branch: claim/{EFFORT}/46" in brief
    assert "Goal: On the EPIF path, the buyer's DM carries the EPIF." in brief
    assert "Attempt 1: engine auto-reply told the session to proceed unattended" in brief
    assert "Attempt 2: engine auto-reply told the session to proceed unattended" in brief
    assert "Attempt 3" not in brief
    assert f"Jules session {SESSION} is in AWAITING_USER_FEEDBACK after 2 auto-replies." in brief
    assert brief.rstrip().splitlines()[-1].startswith("Decision needed: ")
    # The agent's own words are an API response body; they never reach a public repo.
    assert "Does this progress look correct" not in brief


def test_max_auto_replies_comes_from_config():
    actions = evaluate([waiting_session([agent_asks(1)])], config=RepoConfig(max_auto_replies=0))
    assert not of_type(actions, AnswerSessionAction)
    assert len(of_type(actions, EscalateWaitingSessionAction)) == 1


def test_already_stopped_session_is_reported_not_answered_or_escalated_again():
    acts = [agent_asks(1), engine_replied(2), agent_asks(3), engine_replied(4),
            agent_asks(5), engine_stopped(6), agent_asks(7)]
    actions = evaluate([waiting_session(acts)])
    assert not of_type(actions, AnswerSessionAction)
    assert not of_type(actions, EscalateWaitingSessionAction)
    (note,) = of_type(actions, EscalatedSessionStillWaiting)
    assert note.ticket_number == 46
    assert note.claim_ref == f"claim/{EFFORT}/46"


def test_no_second_reply_while_the_session_has_not_answered_the_first():
    # A push-triggered run a minute after the hourly one: the session is still
    # AWAITING_USER_FEEDBACK because it has not processed the engine's reply yet.
    actions = evaluate([waiting_session([agent_asks(1), engine_replied(2)])])
    assert not of_type(actions, AnswerSessionAction)
    assert not of_type(actions, EscalateWaitingSessionAction)


def test_activities_are_ordered_by_create_time_not_list_order():
    # Same history as above, listed newest first: still the engine's reply is last.
    actions = evaluate([waiting_session([engine_replied(2), agent_asks(1)])])
    assert not of_type(actions, AnswerSessionAction)


def test_unknown_history_is_never_answered():
    # The activity fetch failed, so the reply count is unknown.
    actions = evaluate([waiting_session(None)])
    assert not of_type(actions, AnswerSessionAction)
    assert not of_type(actions, EscalateWaitingSessionAction)


def test_other_repos_sessions_and_non_waiting_states_are_left_alone():
    other_repo = waiting_session([agent_asks(1)], repo="owner/TDS-T8")
    working = waiting_session([agent_asks(1)], state="IN_PROGRESS")
    planning = waiting_session([agent_asks(1)], state="PLANNING")
    actions = evaluate([other_repo, working, planning])
    assert not of_type(actions, AnswerSessionAction)
    assert not of_type(actions, EscalateWaitingSessionAction)


def test_session_whose_ticket_is_not_in_this_repo_or_is_done_is_left_alone():
    unknown = waiting_session([agent_asks(1)], number=99)
    assert not of_type(evaluate([unknown]), AnswerSessionAction)
    done = evaluate([waiting_session([agent_asks(1)])], tickets=[ticket(status="done")])
    assert not of_type(done, AnswerSessionAction)


def test_same_number_in_another_effort_is_not_this_ticket():
    other_effort = waiting_session([agent_asks(1)], effort="phase-1")
    assert not of_type(evaluate([other_effort]), AnswerSessionAction)


def test_waiting_session_is_answered_even_when_the_repo_is_paused():
    # Paused means nothing new starts; work in flight still finishes.
    actions = evaluate([waiting_session([agent_asks(1)])], paused=True)
    assert len(of_type(actions, AnswerSessionAction)) == 1
    assert not of_type(actions, StartTicketAction)


def test_config_file_sets_max_auto_replies(tmp_path):
    (tmp_path / ".ticket-engine.toml").write_text("max_auto_replies = 5\n", encoding="utf-8")
    assert load_repo_config(tmp_path).max_auto_replies == 5
    assert load_repo_config(tmp_path / "missing").max_auto_replies == 2


# --- live dispatch --------------------------------------------------------------


def fakes(sessions: list[dict], activities: dict[str, list[dict]], paused: str | None = None):
    github = MagicMock()
    github.get_default_branch_sha.return_value = "base123sha"
    github.list_claim_branches.return_value = [f"claim/{EFFORT}/46"]
    github.get_repo_variable.return_value = paused
    github.get_file_contents.return_value = {"content": TICKET_TEXT, "sha": "blobsha"}
    jules = MagicMock()
    jules.count_recent_sessions.return_value = 3
    jules.list_sessions.return_value = sessions
    jules.list_activities.side_effect = lambda name: activities[name]
    return github, jules


def run(github, jules, tickets=None):
    d = LiveDispatcher(
        repo=REPO, github_client=github, jules_client=jules, config=RepoConfig(), skill_text="# s"
    )
    tks = tickets if tickets is not None else [ticket(status="ready-for-agent")]
    d.dispatch(tickets=tks)
    return d, build_run_report(tks, d.last_run)


def test_live_dispatch_replies_to_a_waiting_session_and_reports_it():
    working = waiting_session(None, number=47, state="IN_PROGRESS")
    working["name"] = "sessions/9047"
    github, jules = fakes(
        [waiting_session(None), working], {SESSION: [agent_asks(1)]}
    )
    _, report = run(github, jules, tickets=[ticket(), ticket(47)])

    jules.list_activities.assert_called_once_with(SESSION)
    jules.send_message.assert_called_once()
    name, message = jules.send_message.call_args.args
    assert name == SESSION
    assert message.startswith(AUTO_REPLY_MARKER)
    assert (
        "- 💬 Ticket 46: its Jules session stopped to ask a question; "
        "the engine told it to proceed unattended (reply 1 of 2)." in report
    )
    github.commit_file_change.assert_not_called()


def test_live_dispatch_escalates_on_the_claim_branch_then_stops_the_session():
    acts = [agent_asks(1), engine_replied(2), agent_asks(3), engine_replied(4), agent_asks(5)]
    github, jules = fakes([waiting_session(None)], {SESSION: acts})
    _, report = run(github, jules)

    claim = f"claim/{EFFORT}/46"
    path = f".scratch/{EFFORT}/issues/46-epif-dm.md"
    github.get_file_contents.assert_called_once_with(repo=REPO, path=path, ref=claim)
    commit = github.commit_file_change.call_args.kwargs
    assert commit["branch"] == claim
    assert commit["path"] == path
    assert commit["sha"] == "blobsha"
    assert "**Status:** blocked" in commit["content"]
    assert "## Escalation — " in commit["content"]
    name, message = jules.send_message.call_args.args
    assert name == SESSION
    assert message.startswith(STOP_MARKER)
    assert (
        f"- ⛔ Ticket 46 escalated: its Jules session kept asking for input after 2 "
        f"auto-replies. Brief is on `{claim}`. Answer the session in Jules, or rework the "
        "ticket and delete the claim branch to retry." in report
    )
    github.delete_branch.assert_not_called()


def test_failed_escalation_commit_does_not_stop_the_session():
    acts = [agent_asks(1), engine_replied(2), agent_asks(3), engine_replied(4), agent_asks(5)]
    github, jules = fakes([waiting_session(None)], {SESSION: acts})
    github.commit_file_change.side_effect = OSError("HTTP 409")
    _, report = run(github, jules)

    jules.send_message.assert_not_called()
    assert "- ⚠️ Ticket 46: could not escalate its waiting Jules session (HTTP 409)." in report


def test_already_escalated_session_is_reported_every_run():
    acts = [agent_asks(1), engine_replied(2), agent_asks(3), engine_replied(4),
            agent_asks(5), engine_stopped(6)]
    github, jules = fakes([waiting_session(None)], {SESSION: acts})
    _, report = run(github, jules)

    jules.send_message.assert_not_called()
    github.commit_file_change.assert_not_called()
    assert (
        f"- ⛔ Ticket 46 is still escalated: its Jules session is waiting on a question. "
        f"Brief is on `claim/{EFFORT}/46`." in report
    )


def test_failed_activity_fetch_means_no_reply():
    github, jules = fakes([waiting_session(None)], {})
    jules.list_activities.side_effect = OSError("HTTP 500")
    run(github, jules)
    jules.send_message.assert_not_called()


def test_paused_repo_still_answers_its_waiting_session_but_starts_nothing():
    github, jules = fakes([waiting_session(None)], {SESSION: [agent_asks(1)]}, paused="true")
    _, report = run(github, jules, tickets=[ticket(), ticket(48)])

    jules.send_message.assert_called_once()
    jules.create_session.assert_not_called()
    github.create_claim_branch.assert_not_called()
    assert "(reply 1 of 2)" in report


# --- Jules adapter contract -------------------------------------------------------


def _resp(body: dict | None):
    r = MagicMock()
    r.read.return_value = b"" if body is None else json.dumps(body).encode("utf-8")
    r.__enter__.return_value = r
    return r


def test_send_message_posts_the_prompt_to_the_session():
    client = JulesClient(api_key="k")
    with patch("urllib.request.urlopen", return_value=_resp(None)) as op:
        client.send_message("sessions/9046", "hello")
    req = op.call_args.args[0]
    assert req.method == "POST"
    assert req.full_url == "https://jules.googleapis.com/v1alpha/sessions/9046:sendMessage"
    assert json.loads(req.data.decode("utf-8")) == {"prompt": "hello"}
    assert req.headers["X-goog-api-key"] == "k"


def test_send_message_accepts_a_bare_session_id():
    client = JulesClient(api_key="k")
    with patch("urllib.request.urlopen", return_value=_resp(None)) as op:
        client.send_message("9046", "hello")
    assert op.call_args.args[0].full_url.endswith("/v1alpha/sessions/9046:sendMessage")


def test_list_activities_follows_page_tokens():
    client = JulesClient(api_key="k")
    pages = [
        _resp({"activities": [agent_asks(1)], "nextPageToken": "p2"}),
        _resp({"activities": [engine_replied(2)]}),
    ]
    with patch("urllib.request.urlopen", side_effect=pages) as op:
        acts = client.list_activities("sessions/9046")
    assert acts == [agent_asks(1), engine_replied(2)]
    urls = [c.args[0].full_url for c in op.call_args_list]
    assert urls[0] == "https://jules.googleapis.com/v1alpha/sessions/9046/activities?pageSize=100"
    assert urls[1] == (
        "https://jules.googleapis.com/v1alpha/sessions/9046/activities?pageSize=100&pageToken=p2"
    )


# --- the skill every worker receives ---------------------------------------------


def test_bundled_skill_tells_the_worker_never_to_ask_and_has_no_hand_back_phrasing():
    bundled = pathlib.Path(__file__).parents[1] / "src/ticket_engine/resources/ticket_skill.md"
    skill = load_ticket_skill(bundled)
    assert "**You are unattended.**" in skill
    assert "- Ask for confirmation, approval or feedback, or wait for a reply." in skill
    # Phrasing that reads as talking to a listener, and made a Jules worker pause.
    for phrase in ("stop and report", "Stop and report", "say so and stop",
                   "State your split decision", "flag it;"):
        assert phrase not in skill, phrase
