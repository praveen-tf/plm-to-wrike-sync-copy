---
name: create-readme
description: Create a new README for this project following the project's conventions
agent: readme-manager
---

# Create README

Before launching the readme-manager, do the following quickly:

1. Run `ls` in the project folder to get a brief overview of the project structure and main language/framework. This will be provided as a summary to the agent.

Then launch the readme-manager with this task, including the summary:
"Create a new README for this project. Here is a brief overview of the project structure: <paste summary here>. Path/scope hint from user (if any): $ARGUMENTS"

Do not spend time deeply researching the project — the readme-manager will handle that.
The agent will explore the project and generate a complete README following project conventions.
