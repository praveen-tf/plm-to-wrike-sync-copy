---
name: execute-spec
description: Execute a spec file, implementing all tasks and dispatching sub-agents as needed
model: sonnet
---

# Execute Spec

Implement the spec specified by the user end-to-end, dispatching sub-agents where appropriate.

## Spec to Execute

$ARGUMENTS

## Instructions

### 1. Read the Spec and Project Docs

- Read the spec file from `specs/` (e.g. `specs/001-feature-name.md`)
- Read every document listed in the spec's **Reference Documents** section before writing any code
- Check `project_docs/` (located at `<your-project>/project_docs/` — run `glob **/project_docs` to find it) for any existing docs on APIs, packages, or services the spec touches. Read them before implementing the relevant tasks — do not rely on training knowledge when project docs are available
- If a task involves an external API or package with no existing project doc and no reference in the spec, dispatch the **project-docs-researcher** agent to fetch it before implementing that task
- If the spec is missing or ambiguous, stop and ask the user to clarify before proceeding

### 2. Implement

- Work through the **Implementation Checklist** in order, respecting task dependencies
- Dispatch sub-agents as appropriate for specialized work (e.g. API integrations, complex utilities)
- If you encounter something unexpected that requires a decision, update the spec with a `> **DECISION NEEDED:**` callout and ask the user before continuing
- Update the spec as you go — check off completed tasks and note any deviations

### 3. Update the README (if needed)

- If the changes introduce new features, alter setup steps, change configuration, or affect how a user or developer would interact with the project, dispatch the readme-manager sub-agent summary of what changed and why it affects the README
- This can be done in parrelel to other implementation tasks

### 4. Verify Acceptance Criteria

- Run through every item in the spec's **Acceptance Criteria** section
- Run tests if applicable (`uv run pytest`)
- Fix any failures before checking in with the user

### 5. Check In with the User

- Summarize what was implemented, referencing the checklist tasks completed
- List any deviations from the spec or open decisions
- Ask: "Everything looks good — shall I commit and create a PR?"

Do not commit or push until the user explicitly approves in step 5.
