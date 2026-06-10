---
name: create-spec
description: Create a spec for a new feature or project following the project's spec conventions
model: sonnet
---

# Create Spec

Create a spec for the feature or project described by the user.

## Instructions

1. Read `.claude/skills/create-spec/SKILL.md` for the spec format, template, and principles
2. Check `specs/` to find the next available number prefix (e.g. if `002-*.md` exists, use `003`)
3. Draft a complete spec saved to `specs/NNN-short-name.md`

Feature or project to spec: $ARGUMENTS

Do not begin implementation — the spec is a planning artifact for human review before any code is written.
