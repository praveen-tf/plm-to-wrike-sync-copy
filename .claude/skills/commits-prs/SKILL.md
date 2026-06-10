---
name: commits-prs
description: Step-by-step procedures for committing code and creating pull requests using conventional commits and gh CLI. Use when handling any git commit or PR tasks to ensure consistency and quality in git workflows.
---

# Git Commit and PR Skill

## Repo Structure

The working environment often uses a **nested git repo pattern**:

```
/workspaces/dotfiles/          ← outer dotfiles repo (do NOT commit here)
    .pre-commit-config.yaml    ← shared pre-commit config for all projects
    .claude/
    <active-project>/          ← the project being worked on (its own git repo)
        .git/
        ...
```

**All commits, branches, and PRs target the `<active-project>`** (the nested folder), not the dotfiles repo. Before any git operation, confirm you are inside the project directory — `git rev-parse --show-toplevel` must NOT return `/workspaces/dotfiles`.

The `.pre-commit-config.yaml` lives in the dotfiles root, one level above the project. Run prek from inside the project directory, pointing at the parent config:

```bash
uv run prek run --all-files --config ../.pre-commit-config.yaml
```

## Section 1 — Pre-Commit Quality Gates

1. Confirm you are in the project directory (not dotfiles root): `git rev-parse --show-toplevel`
2. Run tests (if not already run): `uv run pytest`
3. Format code: `uv run ruff format .`
4. Lint and auto-fix: `uv run ruff check --fix .`
5. Check for `.pre-commit-config.yaml` — look in the project dir first, then the parent:
   ```bash
   ls .pre-commit-config.yaml 2>/dev/null || ls ../.pre-commit-config.yaml 2>/dev/null
   ```
6. If found, run hooks:
   - Config in project dir: `uv run prek run --all-files`
   - Config in parent (dotfiles root): `uv run prek run --all-files --config ../.pre-commit-config.yaml`
7. If any step fails: fix the issue and re-stage before proceeding — never use `--no-verify`

## Section 3 — Branch Strategy

**CRITICAL: Never commit directly to `main`.** Always create a feature branch first, commit there, then create a PR.

Branch naming convention:
- `feature/PROJ-123-short-description`
- `fix/PROJ-456-bug-name`
- `docs/PROJ-789-update-readme`
- `refactor/PROJ-101-cleanup-auth`
- `test/PROJ-202-add-coverage`

If no Jira ticket is known, use the spec number or a short description (e.g. `002-remove-funding-and-otp`).

Always ask for the Jira ticket number if not already known — include it in both the branch name and commit message.

## Section 4 — Commit Workflow

1. **`cd` into the project directory first** — run `git rev-parse --show-toplevel` to confirm you are in the correct repo, not the dotfiles root
2. Run `git status` and `git branch --show-current`
3. **If on `main`, create a feature branch before committing:**
   ```bash
   git checkout -b <branch-name>
   ```
4. Ask the user for the Jira ticket number (always, if not already provided in context)
5. Stage specific files with `git add <file>` — never `git add .` blindly; review `git status` first
6. Run `git diff --staged` — read and understand what is being committed
7. Draft the commit message in conventional format (see below)
8. Run `git commit` with a multi-line message
9. Run `git log --oneline -3` — verify the commit landed correctly

### Commit Message Format

```
<type>(<scope>): <subject> [PROJ-123]

- change 1: why this was needed
- change 2: why this was needed
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`

Focus on the **WHY** behind each change, not just the what.

## Section 5 — PR Workflow

**Before anything else:**
1. **`cd` into the project directory** — run `git rev-parse --show-toplevel` and confirm it is NOT `/workspaces/dotfiles`
2. Run `git branch --show-current` — **if you are on `main`, STOP. You must be on a feature branch to create a PR.** If commits are already on `main`, create a branch from `main` (`git checkout -b <branch-name>`) — this will carry the commits with it.
3. Run `git log origin/main..HEAD --oneline` — confirm there are commits ahead of remote main. If empty, the branch has nothing to PR.

**Then proceed:**
4. Push branch to remote: `git push -u origin $(git branch --show-current)`
5. Review commits in this PR: `git log main...HEAD --oneline`
6. Review files changed: `git diff main...HEAD --stat`
7. If a Jira ticket number is known, call `mcp__claude_ai_Atlassian__getJiraIssue` to fetch ticket details; degrade gracefully if MCP is unavailable
8. Draft PR title: `<type>(<scope>): <subject> [PROJ-123]` (keep under 70 characters)
9. Draft PR body using this template:

```
## Summary
[2-4 sentences: WHAT changed and WHY]

## Jira Ticket
[PROJ-123](link) - Ticket title

## Changes
- change 1 and why
- change 2 and why
```

10. Create the PR as a draft:

```bash
gh pr create --draft \
  --title "..." \
  --body "..." \
  --base main \
  --assignee @me
```

11. Output the PR URL to the user

### Edge Cases

- **Dirty working tree when `/create-pr` is called**: warn the user and offer to commit first or stash changes
- **No `.pre-commit-config.yaml`**: skip the pre-commit step, still run pytest and ruff
- **Atlassian MCP unavailable**: skip ticket fetch, ask the user to provide Jira context manually
- **Single commit in PR**: use that commit message directly as the PR title
- **Multiple commits**: synthesize a summary PR title from the overall change set
- **Commits accidentally on `main`**: create a feature branch from `main` (the commits come with it), push the branch, then create the PR. Do NOT try to rewrite history.

## Section 5 — Common Reference Commands

```bash
gh pr status                          # check PR status
gh pr view --web                      # open PR in browser
gh pr ready                           # mark draft as ready for review
gh pr edit --add-reviewer username    # request a reviewer
gh pr merge --squash --delete-branch  # merge and clean up branch
```
