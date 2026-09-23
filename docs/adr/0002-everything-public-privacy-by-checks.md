# ADR 0002 — Everything public; privacy enforced by checks, not by hiding

**Status:** accepted
**Date:** 2026-09-23
**Applies to:** the engine and every target repo

## Context

All three target repos are public, and the developer wants to keep them public
and to publish the engine too, to show the setup. An earlier draft made the engine
private to hide the process. That fails for a mechanical reason: a public repo
cannot call reusable GitHub workflows stored in a private repo, so a private
engine would have to be smuggled in with access tokens and checked-out copies.

The real privacy risks are narrow and specific: API keys and tokens; lab members'
names, Slack IDs and emails appearing in tickets or docs (one already had); and
operational details of the lab's tools. Hiding a whole repo is a blunt answer to
three sharp problems.

## Decision

1. **The engine and all target repos are public.** Target repos call the engine's
   reusable workflows directly, pinned to a version tag.
2. **Three layers enforce privacy:**
   - GitHub secret scanning with push protection, on every repo.
   - A people-denylist scan in the integrity gate. The denylist is a GitHub
     secret, never a committed file, because a committed denylist is itself the
     leak.
   - A written rule in every `AGENTS.md` and the ticket template: refer to
     **roles**, never to people.
3. **Secrets live in one `secrets.env` outside every repo** on the developer's
   machine. The bootstrap copies them into GitHub secrets. Only the key names are
   ever committed.

## Consequences

- Engine logs in public Actions runs are public. Scripts must never print a
  secret, a prompt containing one, or denylist contents.
- Removing a leaked detail from a file does not remove it from git history. The
  checks exist to stop it landing in the first place.
- If a target repo later needs to go private, only its CI-minute budget changes;
  the engine's design does not.
