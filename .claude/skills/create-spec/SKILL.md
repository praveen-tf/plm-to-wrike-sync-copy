---
name: create-spec
description: Step-by-step procedures for creating specs following the project's spec-driven development conventions. Use when creating the plan for new projects or features.
---

# Create Spec Skill

This guideline skill how the coding agent should generate feature, change, and project specifications. Each spec is a **single markdown document** stored in `specs/`. Specs serve as the contract between the human (intent) and the agent (implementation). They define the "what" before the "how," direct the agent to existing reference material, and provide a trackable implementation checklist.

## When to Write a Spec

- Write a spec when the change involves **two or more of**: new files, new dependencies, changes to multiple existing files, new external API integrations, or meaningful changes to data flow.
- Skip specs for single-file bug fixes, config tweaks, or cosmetic changes — describe those inline.
- For a large new project or major refactoring, make multiple specs breaking down the work into logical components.

## File Naming and Location

```
specs/
  NNN-short-name.md       # e.g. 001-donor-notes-capture.md
```

Use a zero-padded incrementing number prefix. Name should be lowercase, hyphenated, and descriptive enough to identify the feature at a glance.

---

## Researching External Dependencies (Run in Parallel with Drafting)

As you plan the spec, identify key external APIs, packages, or services the feature will rely on — then **dispatch project-docs-researcher agents in parallel** for any that aren't already documented. Don't wait for them to finish before starting the spec draft; work on both simultaneously and incorporate the results into Section 4 as they come back.

**First, locate the project's `project_docs/` folder.** It lives inside the active project, not the dotfiles root — typically at `<your-project>/project_docs/`. Run `glob **/project_docs` to find it. If it doesn't exist yet, it will be created by the researcher on first invocation.

For each external dependency identified:

1. **Check `project_docs/`** — if a doc already exists, read it and plan to reference it in Section 4
2. **If no doc exists**, dispatch a **project-docs-researcher** agent (run multiple in parallel if there are several dependencies):

   > "Research `<library/API name>` and save a summary to `project_docs/`. Project context: [what the project does and how this dependency will be used — e.g. which endpoints, auth pattern, key methods]. Focus on: [specific aspects relevant to this feature]."

3. Continue drafting the spec while researchers run — fill in Section 4 references once docs are saved

**Dispatch project-docs-researcher when:**
- The feature integrates a new external API or third-party service
- A new package is being introduced to the project
- An existing dependency is being used in a new way not yet documented

**Skip when:**
- The feature only touches internal code with no new external dependencies
- `project_docs/` already has a current, relevant doc for the dependency

---

## Spec Template

Every spec follows this structure. Draft the full document — the human reviews and refines before implementation begins.

```markdown
# [NNN] Feature Name

**Status:** draft | approved | in-progress | complete
**Created:** YYYY-MM-DD
**Last updated:** YYYY-MM-DD

---

## 1. Overview

A 2-4 sentence summary of what this feature does and why it matters.
State the problem being solved and for whom.

## 2. Requirements

Describe the functional requirements as concrete, testable statements.
Use WHEN/THEN phrasing where possible to make acceptance unambiguous.

- WHEN [trigger/condition], THEN [expected behavior]
- WHEN [trigger/condition], THEN [expected behavior]

### Out of Scope

Explicitly list what this spec does NOT cover to prevent scope creep.

## 3. Design

### Affected Files

Map changes to the project's layer structure:

| Layer | File | Change |
|-------|------|--------|
| Entry point | `main.py` | Add CLI flag for new feature |
| Core logic | `src/class_1.py` | New method `process_x()` |
| Helpers | `src/utils.py` | Add `format_x()` helper |
| Config | `src/config.py` | New env var `X_API_KEY` |
| Tests | `tests/test_class_1.py` | Tests for `process_x()` |
| Deploy | `deploy.py` | No change |

### Key Decisions

State important technical choices and the reasoning behind them.
Keep this brief — 1-3 sentences per decision. For example:

- **Data format:** Using JSON over CSV because [reason].
- **Error handling:** Retry with exponential backoff because [reason].

## 4. Reference Documents

Point the agent to existing docs that contain details needed for
implementation. The agent MUST read these before writing code.

| Document | Location | What to look for |
|----------|----------|------------------|
| API auth guide | `project_docs/api_auth.md` | Auth headers, token refresh |
| Logging standards | `agent_docs/logging_guidelines.md` | Log format, levels to use |
| Test conventions | `agent_docs/testing_guidelines.md` | Fixture patterns, naming |

> **Rule:** Do not duplicate information that already exists in
> `project_docs/` or `agent_docs/`. Reference it, summarize the
> relevant parts in one line, and move on.

## 5. Implementation Checklist

Break the work into small, ordered tasks. Each task should be
completable and testable independently. Mark dependencies explicitly.

- [ ] **Task 1: [short description]**
      Files: `src/config.py`
      Details: Add `X_API_KEY` to config with env var fallback.
      Ref: `project_docs/env_vars.md`

- [ ] **Task 2: [short description]** *(depends on: Task 1)*
      Files: `src/utils.py`
      Details: Implement `format_x()` that [does what].
      Test: Unit test with [these edge cases].

- [ ] **Task 3: [short description]** *(depends on: Task 1)*
      Files: `src/class_1.py`
      Details: Add `process_x()` method that [does what].
      Ref: `project_docs/api_reference.md` for endpoint details.
      Test: Unit test covering [happy path, error case].

- [ ] **Task 4: Wire into entry point** *(depends on: Tasks 2, 3)*
      Files: `main.py`
      Details: Add CLI flag `--feature-x` that invokes `process_x()`.

- [ ] **Task 5: Integration test** *(depends on: Task 4)*
      Files: `tests/test_class_1.py`
      Details: End-to-end test with mock API responses.

- [ ] **Task 6: Update docs and config**
      Files: `.env`, `requirements.txt`
      Details: Add new dependency, document new env var.

## 6. Acceptance Criteria

How the human will verify the feature is complete:

- [ ] All checklist tasks done
- [ ] All tests pass (`pytest tests/`)
- [ ] No new linting errors
- [ ] Feature works end-to-end with [specific manual verification step]
- [ ] Relevant project_docs updated if API usage changed
```

---

## Principles for Writing the Spec

1. **Be concise.** The spec is a map, not a novel. Each section should be the minimum needed to remove ambiguity. If a section would exceed ~15 lines, you're probably duplicating info that belongs in `project_docs/` — reference it instead.

2. **Reference, don't repeat.** If implementation details exist in `project_docs/` or `agent_docs/`, add a row to the Reference Documents table and summarize in one sentence what the agent should look for there. Never copy-paste documentation into the spec.

3. **Make requirements testable.** Every requirement in Section 2 should map to at least one checklist task in Section 5 and one acceptance criterion in Section 6. If you can't describe how to test a requirement, it's too vague.

4. **Surface unknowns early.** If something is ambiguous or needs a human decision, flag it directly in the spec with a `> **DECISION NEEDED:** ...` callout. Do not guess.

5. **Scope aggressively.** The "Out of Scope" subsection is mandatory. It prevents the agent from gold-plating or building adjacent features that weren't requested.

---

## How This Spec Will Be Used

Once approved, the `/execute-spec` command will implement it. The executing agent will:

- Read every document in **Section 4** before writing any code
- Complete checklist tasks in order, respecting dependencies
- Commit after each task and update the spec as it goes
- Stop when all acceptance criteria pass — no extras

Write the checklist with this in mind: tasks should be small enough to commit independently and specific enough that the agent knows exactly what "done" looks like.

---

## Example: Minimal Spec

For smaller features, sections can be brief:

```markdown
# 003 Add retry logic to API calls

**Status:** draft
**Created:** 2025-03-07
**Last updated:** 2025-03-07

---

## 1. Overview

API calls to the donor service occasionally fail with 429/503 errors.
Add retry with exponential backoff to improve reliability.

## 2. Requirements

- WHEN an API call returns 429 or 503, THEN retry up to 3 times with
  exponential backoff (1s, 2s, 4s).
- WHEN all retries are exhausted, THEN raise a descriptive exception.
- WHEN a non-retryable error occurs (4xx other than 429), THEN fail
  immediately with no retry.

### Out of Scope

Circuit breaker pattern, request queuing, retry on network timeouts.

## 3. Design

### Affected Files

| Layer | File | Change |
|-------|------|--------|
| Helpers | `src/utils.py` | New `retry_request()` decorator |
| Core logic | `src/class_1.py` | Apply decorator to API calls |
| Tests | `tests/test_utils.py` | Retry logic tests |

### Key Decisions

- **Using a decorator** so retry logic is reusable without changing every call site.
- **tenacity library** for retry implementation — already in ecosystem, well-tested.

## 4. Reference Documents

| Document | Location | What to look for |
|----------|----------|------------------|
| API error codes | `project_docs/donor_api.md` | Retryable vs fatal codes |
| Logging | `agent_docs/logging_guidelines.md` | How to log retries |

## 5. Implementation Checklist

- [ ] **Task 1:** Add `tenacity` to `requirements.txt`
- [ ] **Task 2:** Implement `retry_request()` in `src/utils.py`
      Ref: `project_docs/donor_api.md` for error code list
- [ ] **Task 3:** Apply decorator to API methods in `src/class_1.py`
      *(depends on: Task 2)*
- [ ] **Task 4:** Add tests for retry behavior in `tests/test_utils.py`
      *(depends on: Task 2)*

## 6. Acceptance Criteria

- [ ] 429/503 responses trigger retry with backoff
- [ ] Non-retryable errors fail immediately
- [ ] All retries are logged at WARNING level
- [ ] Tests pass with mocked API responses
```
