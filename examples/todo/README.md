# A real dotagent Todo demo

A dependency-free Node app, synthetic data and a Docker volume. It deliberately
starts without task filters so dotagent has a small, observable feature to build.

```sh
node --test
docker compose -f compose.json up -d
# Open http://localhost:8080
```

For a personal dotagent run, copy this folder into a **separate Git repository**,
commit it and push to a GitHub repository you control. Register its actual paths
and verification commands using `project.example.toml`. Screenshots belong in the
runtime artifact directory, never in this folder.

From Claude Code:

```text
/dotagent Add accessible All, Active and Done filters. Preserve keyboard access,
verify against the running app, and deliver a draft PR with screenshots.
```

From Codex:

```text
$dotagent Add accessible All, Active and Done filters. Preserve keyboard access,
verify against the running app, and deliver a draft PR with screenshots.
```

For the remote demonstration, enable `[github_mentions]`, create an issue with
those criteria, then comment `@dotagent work on this issue`. Keep the Mac worker
running. The first poll begins watching new comments; use an explicit `since`
only when you intentionally want to import old mentions.

The app and workflow screenshots in the root README come from the actual demo
run. The separately labelled promotional hero is conceptual artwork.
