"""Prompt assembly and ticket skill loading.

WHY THIS EXISTS
---------------
Phase 1 Spec §Implementation Decisions (Prompt) and Ticket 02 require that
the dispatcher can assemble the exact prompt a Jules session receives for a
given ticket.

The prompt brings together:
1. The runner-agnostic ticket skill instructions.
2. The target repository name and exact ticket path.
3. The strict orientation order: read AGENTS.md, the ticket file, ADRs in
   docs/adr/, and CONTEXT.md first.
4. An unattended-run rule: never stop to ask for confirmation. Jules sessions are
   created with plan approval off, but the agent could still pause on its own to ask
   "does this plan look correct?", which stalls the ticket overnight because nobody
   answers. The rule lives here, not only in the skill, because `load_ticket_skill`
   prefers the target repo's own copy of the skill, which the engine does not control.
5. A required final step: update this ticket's file (Status: done, every criterion
   ticked, a dated summary under ## Comments) in the same PR. Jules merged two tickets
   without doing this, because the skill only told local workers to; the tickets then
   read as unfinished, kept their claims, and blocked the queue. It lives here for the
   same reason as rule 4, and the integrity gate fails any PR that skips it.
6. An optional handoff section (ADR 0006 rule 6, ticket 25) when the box already
   pushed a checkpoint for this ticket before it was released to Jules. The Jules
   session still starts from the default branch, never the checkpoint branch,
   because the PR Jules opens must have the default branch as its base; merging
   the checkpoint into the session's own working copy is how it picks up where the
   box left off without changing that base. The merge is best effort by design: if
   the box's branch has diverged in a way that cannot fast-forward or auto-merge,
   the worker starts the ticket fresh rather than getting stuck resolving another
   worker's conflict.

CRITICAL PRIVACY AND LOGGING INVARIANT:
As required by ADR 0002 and the Phase 1 Spec: Prompts and secrets must NEVER
be logged or written to stdout/stderr. Actions logs on public repositories are
public, and prompts can contain operational context. Prompt assembly must
remain a pure function without I/O or logging side effects.
"""
from __future__ import annotations

import pathlib
import re
from importlib import resources

from ticket_engine.dispatch import Handoff

_DEFAULT_SKILL_REL_PATH = pathlib.Path(".agents/skills/ticket/SKILL.md")


def load_ticket_skill(skill_path: pathlib.Path | str | None = None) -> str:
    """Load the runner-agnostic ticket skill markdown text.

    If skill_path is provided, loads from that path.
    Otherwise, checks the repository's `.agents/skills/ticket/SKILL.md`,
    or falls back to the package's bundled resource.
    """
    if skill_path is not None:
        p = pathlib.Path(skill_path)
        return p.read_text(encoding="utf-8")

    # Check relative to current working directory or git root
    candidate = pathlib.Path.cwd() / _DEFAULT_SKILL_REL_PATH
    if candidate.is_file():
        return candidate.read_text(encoding="utf-8")

    # Check relative to module location
    module_dir = pathlib.Path(__file__).resolve().parent
    repo_root_guess = module_dir.parent.parent
    candidate_repo = repo_root_guess / _DEFAULT_SKILL_REL_PATH
    if candidate_repo.is_file():
        return candidate_repo.read_text(encoding="utf-8")

    # Check bundled resource in resources/ticket_skill.md
    bundled_file = module_dir / "resources" / "ticket_skill.md"
    if bundled_file.is_file():
        return bundled_file.read_text(encoding="utf-8")

    try:
        # Fall back to importlib.resources
        files_res = resources.files("ticket_engine.resources") / "ticket_skill.md"
        return files_res.read_text(encoding="utf-8")
    except Exception as exc:
        msg = f"Could not find ticket skill document at {candidate} or in package resources."
        raise FileNotFoundError(msg) from exc


_UNATTENDED_RULE = (
    "This session runs unattended. No human reads it or will reply to it. "
    "Do not ask for confirmation, approval, or feedback on your plan "
    '(for example "Does this plan look correct?"). Make the decision yourself from the '
    "ticket, its ADRs, and AGENTS.md, then proceed. If a question truly blocks you "
    "(the ticket is ambiguous in a way that changes the result, or it needs bench work "
    "or a human judgement call), do not wait for an answer: set the ticket's `Status:` "
    "to `blocked`, write the escalation brief described below under `## Comments`, "
    "and finish the session."
)


def _final_step_rule(ticket_path: str) -> str:
    return (
        f"Before you finish, update the ticket file '{ticket_path}' in this same PR. "
        "This is required, not optional:\n"
        "1. Set its status line to `Status: done`.\n"
        "2. Tick every acceptance criterion you implemented and verified: `- [ ]` becomes `- [x]`. "
        "Tick only what a test or check you ran actually covers; if a criterion is not met, "
        "the ticket is not done, so escalate instead of ticking it.\n"
        "3. Under `## Comments`, replace any progress note with a dated summary: what was "
        "built and which tests cover which criterion.\n"
        "Keep the ticket's Claimed-by: line exactly as it is.\n"
        "The integrity gate fails any PR that does not change its ticket file this way, "
        "and the PR cannot merge until it does."
    )


def _handoff_section(handoff: Handoff) -> str:
    return (
        f"A previous worker pushed a checkpoint to branch {handoff.branch}.\n"
        f"Run: git fetch origin {handoff.branch} && git merge --no-edit FETCH_HEAD\n"
        "If that fails, start the ticket fresh from the default branch and say so under ## Comments.\n"
        f"{handoff.note}"
    )


def assemble_prompt(
    skill_text: str, repo: str, ticket_path: str, handoff: Handoff | None = None
) -> str:
    """Assemble the prompt for an implementing worker (such as Jules).

    Takes the runner-agnostic skill text, the target repository name, and the
    ticket path, and returns the full prompt. When `handoff` is given, a
    checkpoint section tells the worker to fetch and merge the branch the box
    already pushed for this ticket and continue from its progress note
    (ADR 0006 rule 6). Without a handoff, the output is unchanged.

    In accordance with ADR 0002 and the Spec, this function does NO logging,
    printing, or external I/O.
    """
    clean_ticket_path = str(ticket_path).replace("\\", "/").strip()
    clean_repo = str(repo).strip()

    prompt_parts = [
        f"You are implementing a ticket for the repository '{clean_repo}'.",
        f"Ticket file: {clean_ticket_path}",
        "",
        "## ORIENTATION ORDER (READ FIRST BEFORE TOUCHING CODE)",
        "Read, in this order:",
        "1. AGENTS.md — the whole file: invariants, layering, conventions, and active plan.",
        f"2. The ticket file at '{clean_ticket_path}'. Its 'Blocked by:' line and acceptance criteria are the contract.",
        "3. Every ADR referenced by the ticket in 'docs/adr/'. ADRs are binding, not background.",
        "4. CONTEXT.md — the domain glossary. Use its words exactly.",
        "5. The module docstring of every file you are about to edit. Docstrings explain why.",
        "",
        "## UNATTENDED RUN — NO HUMAN IS WATCHING",
        _UNATTENDED_RULE,
        "",
    ]

    if handoff is not None:
        prompt_parts.extend(
            [
                "## HANDOFF — CONTINUE FROM A CHECKPOINT",
                _handoff_section(handoff),
                "",
            ]
        )

    prompt_parts.extend(
        [
            "## FINAL STEP — MARK THE TICKET DONE IN THIS PR",
            _final_step_rule(clean_ticket_path),
            "",
            "## TICKET IMPLEMENTATION SKILL AND RULES",
            skill_text.strip(),
        ]
    )

    return "\n".join(prompt_parts)


def extract_progress_note(ticket_text: str) -> str:
    """Extract the most recent progress note from a ticket's ## Comments section.

    Shared by the live dispatcher (for a box's handoff to Jules, ADR 0006 rule 6)
    and the local worker's own checkpoint/resume prompt, so both read a ticket's
    progress note the same way.
    """
    match = re.search(r"^##\s+Comments\s*\n(.*?)(?=^##|\Z)", ticket_text, re.MULTILINE | re.DOTALL)
    if not match:
        return ""
    return match.group(1).strip()


def assemble_fix_prompt(
    repo: str, ticket_path: str, failures: list[tuple[str, str]]
) -> str:
    """Assemble the prompt for a CI-fix run on a ticket that is already done.

    Ticket 71 (ADR 0011 rules 1 and 2, spec box-fix-loop): `assemble_prompt` tells
    a worker to check the frontier, claim the ticket and set `in-progress`. On a
    ticket that is already `done` and already claimed, that lets agy reasonably
    stop without doing anything. This prompt says plainly what the situation is
    and carries each failing check's real output, and it deliberately omits the
    ticket skill.

    `failures` is `(name, output)` pairs, rendered as `### <name>` followed by the
    output in a fenced block. Pure: no I/O, no logging.
    """
    clean_ticket_path = str(ticket_path).replace("\\", "/").strip()
    parts = [
        f"You are fixing failing CI for a ticket in the repository '{str(repo).strip()}'.",
        f"Ticket file: {clean_ticket_path}",
        "",
        "## THIS TICKET IS ALREADY YOURS AND DONE",
        (
            "The ticket is claimed by you and its Status is done. Do not run "
            "scripts/check_claimable.py, do not create or change any claim, and do not "
            "change the Status line. Fix only the failures below, then commit."
        ),
        "",
        _UNATTENDED_RULE,
        "",
        (
            "Never delete, skip, xfail or weaken a test, and never raise a ratchet, "
            "to make a check pass."
        ),
        "",
        "## CI FAILED — FIX IT",
    ]
    for name, output in failures:
        parts.append(f"### {name}")
        parts.append("```")
        parts.append(str(output).strip("\n"))
        parts.append("```")
    return "\n".join(parts)
