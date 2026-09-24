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

CRITICAL PRIVACY AND LOGGING INVARIANT:
As required by ADR 0002 and the Phase 1 Spec: Prompts and secrets must NEVER
be logged or written to stdout/stderr. Actions logs on public repositories are
public, and prompts can contain operational context. Prompt assembly must
remain a pure function without I/O or logging side effects.
"""
from __future__ import annotations

import pathlib
from importlib import resources

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


def assemble_prompt(skill_text: str, repo: str, ticket_path: str) -> str:
    """Assemble the prompt for an implementing worker (such as Jules).

    Takes the runner-agnostic skill text, the target repository name, and the
    ticket path, and returns the full prompt.

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
        "## TICKET IMPLEMENTATION SKILL AND RULES",
        skill_text.strip(),
    ]

    return "\n".join(prompt_parts)
