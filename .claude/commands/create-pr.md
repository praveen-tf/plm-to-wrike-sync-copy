---
name: create-pr
description: Create a pull request for the current branch following the project's git workflow conventions
agent: git-agent
---

# Create Pull Request

## Confirm you are in the project directory (DO NOT CREATE A PR TO THE DOTFILES REPO, INSTEAD LOOK FOR CHANGES IN THE ACTIVE PROJECT)

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

## Deploy PR agent

Before launching the git-agent, do the following quickly:

1. Run `git log main...HEAD --oneline` to get a brief summary of commits in this branch

Then launch the git-agent with this task, including the summary:
"Create a pull request. Here is a summary of the commits in this branch: <paste log output here>. Target branch override (if any): $ARGUMENTS. Default target: main."

Do not spend time deeply researching the changes — the git-agent will handle that.
The agent will analyze commits, draft the PR title and body, and create a draft PR via gh CLI.
