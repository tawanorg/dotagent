---
name: dotagent
description: Work with the persistent dotagent worker from this chat—start a task, give instructions, remember project guidance, inspect progress, or pause and resume work.
disable-model-invocation: true
argument-hint: "task, instructions, status, pause, or resume"
---

Read `~/.local/share/dotagent/INTERACTION.md` and follow it for the user's request.
Use `HOST=claude` when that guide requests a host choice. Interpret the user's
natural language in this conversation; preserve context in the queued request.

User request: $ARGUMENTS
