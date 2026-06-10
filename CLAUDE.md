# CLAUDE.md

## Core Principles

1. **ALWAYS PRIORITIZE READABILITY**: this is the MOST IMPORTANT PRINCIPLE and should be your absolute guiding north star, always prioritize readability even if it makes the code longer or causes some duplication. Humans need to be able to easily read and understand the code.
2. **RESEARCH FIRST**: Always read/research first, understand context fully, scrape the official website, use Context Hub, use deepwiki mcp, etc., to make sure you really understand how APIs, packages, services, etc. work and save your findings in the `project_docs/` folder.
3. **FOLLOW EXISTING PATTERNS**: Don't invent new approaches.
4. **SURGICAL CHANGES**: Touch only what you must.
5. **KISS**: Simplicity over complexity.
6. **YAGNI**: Build only what's needed now.
7. **MAX 3 LAYERS DEEP**: see [Project Architecture](#project-architecture).
8. **NO THIN WRAPPER FUNCTIONS**: DO NOT wrap a couple lines of code in a function just because it gets reused once, this makes it incredibly hard to read the code because things get masked behind these thin wrappers and the user needs to constantly hit control+F to figure out what is in the function. ALWAYS PRIORITIZE READABILITY.

## Guidelines

- **No Dead Code**: Delete unused functions immediately, Git has history if needed.
- **Detailed Errors**: Explicit exceptions over silent failures - identify and fix issues fast.
- for testing guidelines, see `agent_docs/testing_guidelines.md`.
- for configuration management and data model guidelines, see `agent_docs/configs_data_models.md`.
- for logging guidelines, see `agent_docs/logging_guidelines.md`.
- for style and conventions guidelines, see `agent_docs/style_guidelines.md`.
- README: Keep concise - use the readme-manager subagent and follow the guidelines in `.claude/skills/readme-manager/SKILL.md`.
- Commits & PRs: use the git-agent subagent and follow the commit and PR guidelines in `.claude/skills/commits-prs/SKILL.md`.

> **Note on `agent_docs/venv_management_uv.md`:** the foundation guidelines assume `uv`, but this repo is an **Azure Functions** app that deploys with **pip + `requirements.txt`** (Flex Consumption remote build installs server-side). Use `requirements.txt` for runtime dependencies. A local virtualenv (`.venv`) is fine for development and running the test suite.

## Spec Driven Development

- when kicking off a new feature, use plan mode to create a spec in the `specs/` folder.
- review `.claude/skills/create-spec/SKILL.md` for how to write good specs and use them to drive development.
- create specs with `/create-spec`; implement them with `/execute-spec specs/NNN-name.md`.
- completed specs can be moved to `spec_archive/`.

## Project Docs Library

- use the `project_docs/` folder to store documentation for core APIs, packages, and services used in the project (Wrike API, Azure Functions, Service Bus, psycopg/Postgres). This ensures accurate, up-to-date information is always at hand during development.
- review `.claude/skills/project-docs/SKILL.md` for guidelines on how to retrieve and summarize documentation effectively, as well as standards for maintaining the project docs library.
- use the **project-docs-researcher** subagent to research and document any external dependency — dispatch it whenever a new API, package, or service needs to be documented.
- ALWAYS review the project docs to confirm the proper syntax for API calls, library usage, and other details when implementing features that rely on external services or packages. Do not rely on training knowledge alone for these details.

### When to Create or Update the Project Docs Library

- **Before building** — search online, retrieve current docs for core APIs, packages, and services and summarize relevant details.
- **When you encounter unexpected behavior** — check and update docs to reflect what you learn.
- **When the user asks you to correct something** — check and update docs to reflect what you learn.

## Project Architecture

This is an **Azure Functions** app (Python) that syncs PLM data to Wrike, decoupled via a Service Bus queue. It follows the 3-layer architecture with tests co-located in `tests/`:

```
.claude/                # Spec-driven dev commands, skills, agents (from dotfiles-foundation)
agent_docs/             # General development guidelines (shared across projects)
specs/                  # Feature specifications (markdown, versioned)
project_docs/           # Reference docs for Wrike API, Azure, Postgres, etc. (create as needed)

function_app.py         # Layer 1: Entry point — the two Azure Functions
                        #   plm_wrike_producer (timer -> enqueue families)
                        #   plm_wrike_consumer (Service Bus trigger -> create/update Wrike card)
sync.py                 # Layer 2: Core sync orchestration (PLM family -> Wrike card)
mapping.py              # Layer 2: PLM field -> Wrike field mapping
state.py                # Layer 2: Watermarks / task-id map / sync state
loader.py               # Layer 2: Load PLM data into the local Postgres
plm_reader.py           # Layer 2: Read canonical PLM families
wrike_client.py         # Layer 2: Wrike REST client
db.py                   # Layer 3: Database connection helper
description.py          # Layer 3: Wrike description section-merge helper
changes.py              # Layer 3: Change/diff helpers
settings.py            # Layer 3: Configuration and environment variables

host.json               # Azure Functions host config
local.settings.json     # Local Functions settings (gitignored; see .example)
requirements.txt        # Runtime dependencies (pip — installed server-side on deploy)
docker-compose.yml      # Local Postgres for running the test suite
scripts/                # Operational helper scripts (e.g. trigger_azure.sh)
project_docs/           # Reference docs + internal deployment/runbook docs (currently gitignored)
tests/
  conftest.py
  test_sync.py          # Tests for sync.py
  test_mapping.py       # Tests for mapping.py
  ...                   # one test file per module
```

## Available Tools

**CLI**
- `gh` — GitHub (repos, PRs, issues, actions)
- `rg` (ripgrep) — Fast code searching
- `chub` (context-hub) — Fetch up-to-date docs for libraries and APIs; see `.claude/skills/context-hub/SKILL.md`
- `playwright-cli` — Browser automation and web dev testing (via Bash tool)
- `firecrawl` — web scraping, search, crawling, and browser automation CLI; see `.claude/skills/firecrawl-cli/SKILL.md`

**MCP**
- `Context7` — Retrieve up-to-date documentation for libraries, APIs, and services
- `deep-wiki` — Retrieve structured documentation for any GitHub repo
