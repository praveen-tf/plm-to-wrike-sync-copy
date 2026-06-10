---
name: commit
description: Commit the current changes following the project's git workflow conventions
agent: git-agent
---

# Commit Changes

## Confirm you are in the project directory (DO NOT COMMIT TO THE DOTFILES REPO, INSTEAD LOOK FOR CHANGES IN THE ACTIVE PROJECT)

Before any git operation, confirm you are inside the project directory — `git rev-parse --show-toplevel` must NOT return `/workspaces/dotfiles`.

```
/workspaces/dotfiles/          ← outer dotfiles repo (do NOT commit here)
    .pre-commit-config.yaml    ← shared pre-commit config for all projects
    .claude/
    <active-project>/          ← the project being worked on (its own git repo)
        .git/
        ...
```
**All commits, branches, and PRs target the `<active-project>`** (the nested folder), not the dotfiles repo. If you are not in the project folder, use `cd` to navigate into it before running any further commands.

## Deploy commit agent

Before launching the git-agent, do the following quickly:

1. Run `git status` to see what changes are unstaged and get a sense of what you have modified. This will help you provide a useful summary to the agent. If there is anything you are unsure you should commit, ask the agent for guidance on whether it should be included in this commit or saved for a future one.
2. Run `git add .` to stage the changes you have made (or add the specific files you want to include in this commit).

Then launch the git-agent with this task, including the summary:
"Commit the current changes. Here is a summary of what was staged: `<add your changes>. Scope hint from user (if any): $ARGUMENTS"

Do not spend time deeply researching the changes — the git-agent will handle that.
The agent will handle quality checks, branching, review, and committing.
