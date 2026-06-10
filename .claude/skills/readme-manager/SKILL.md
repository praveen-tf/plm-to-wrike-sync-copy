---
name: readme-manager
description: Step-by-step procedures for creating and updating README files following project conventions. Use when handling any tasks related to README creation or updates.
---

# README Manager Skill

## Section 1 — Create Workflow

1. Explore project structure: run `glob **/*` to discover key files
2. Read `pyproject.toml`, `package.json`, `requirements.txt`, or equivalent to identify dependencies and tooling
3. Read key source files to understand what the project does
4. Draft README following the structure in Section 3
5. Write the file to `README.md` (or path specified by user)
6. Report what was created

## Section 2 — Update Workflow

1. Read the current README
2. If no update context was provided by the user, run `git diff --staged` to assess staged changes — update only if changes include: new dependencies, modified setup steps, new env vars, structural changes, or changed deployment process
3. If context was provided by the user, focus updates on the relevant sections only
4. If update scope is still unclear, use `AskUserQuestion` to ask the user where to focus
5. Edit only the sections that need changes — do not rewrite the whole file
6. Report what sections were changed and why

## Section 3 — README Structure

1. **Project Title & Brief Description** (1-2 sentences: what it does and why it exists)
2. **Prerequisites & Setup** (MOST IMPORTANT): required accounts, API keys, system dependencies, database setup, required permissions
3. **Installation**: environment variable configuration, package/dependency installation commands, configuration file setup
4. **Usage/Running the Project**: development mode, production deployment, common commands
5. **Project Structure**: overview of repo layout with concise explanations of what lives where and how components relate
6. **Technology Stack**: simple list of the tech stack

## Section 4 — Style Rules

- Keep it concise and scannable
- Use code blocks for all commands
- Focus on "quick start" approach
- Use `WebFetch` / `WebSearch` to retrieve accurate technical setup instructions when needed
- Avoid: lengthy explanations, license details, contributing guidelines, detailed API docs, exhaustive examples, test instructions, linting instructions

## Section 5 — Edge Cases

- **No existing README when update is requested**: inform the user and offer to create one instead
- **Monorepo with multiple READMEs**: use `AskUserQuestion` to ask the user which path to target
- **Pre-commit context** (called by git-agent during commit flow): run `git diff --staged`, skip update if no README-relevant changes are found
- **No dependency manifest found**: explore source files directly and infer the stack from imports and config files
