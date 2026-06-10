---
name: project-docs
description: Standards for maintaining the project_docs/ library — file naming, doc format, retrieval priority order, and update rules. Use when creating or updating reference documentation for third-party libraries, APIs, or services used in the project.
---

# Project Docs Skill

Maintain a `project_docs/` folder inside the active project (`<your-project>/project_docs/`) as a library of reference documentation for core APIs, packages, and services. This ensures accurate, up-to-date information is always at hand during development.

## Location

`project_docs/` lives **inside the active project's folder**, not at the dotfiles root. Run `glob **/project_docs` to find it. If it doesn't exist yet, create it at `<your-project>/project_docs/`.

## File Naming

Use descriptive, lowercase names that make the content obvious at a glance:
- `stripe_api_docs.md`
- `sqlalchemy_docs.md`
- `pubsub_docs.md`

## Retrieval Priority

When fetching documentation for a library, API, or service:

1. **Official website** — use the firecrawl CLI (`firecrawl scrape <url>` or `firecrawl search "<query>" --scrape`) to fetch current docs directly
2. **chub (context-hub skill)** — `chub search "<library>"` then `chub get <id>` for structured docs (powerful source but not always available for every library)
3. **Context7 MCP** — `mcp__claude_ai_Context7__resolve-library-id` + `mcp__claude_ai_Context7__query-docs` as a fallback
4. **deep-wiki MCP** — `mcp__deep-wiki__read_wiki_structure` + `mcp__deep-wiki__read_wiki_contents` for GitHub repos and source-level understanding
5. **WebSearch** — to find the right URL if the official docs location is unknown

Never rely on training knowledge alone for external APIs or fast-moving packages.

## Doc Format

Each file follows this structure:

```markdown
# <Library / API Name> — Reference Summary

**Source:** <URL>
**Retrieved:** <YYYY-MM-DD>
**Version:** <version if known>

## Overview

1-2 sentences on what this library/API does and why it's used in this project.

## Installation / Setup

<relevant install commands and setup steps>

## Key Concepts

<core abstractions — lean on direct quotes and copy-paste from the source>

## Common Usage Patterns

<patterns most likely needed in this project, with code examples>

## Configuration & Auth

<environment variables, credentials, client initialization>

## Error Handling

<notable error types, retry behavior, status codes>

## Gotchas / Notes

<version quirks, known footguns, project-specific observations>
```

## Rules

- **Project-focused** — document what's relevant to the project, not exhaustive API coverage
- **Summarize, don't recreate** — you can summarize to reduce irrevelant information, but don't recreate things, instead heavily rely on direct copy-paste from the original docs
- **Include source URL and retrieval date** on every doc so it can be refreshed
- **Update when behavior changes** or a new version is adopted
