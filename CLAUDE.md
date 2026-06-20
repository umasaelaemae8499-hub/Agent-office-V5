# CLAUDE.md

Guidance for AI assistants (Claude Code and others) working in this repository.

## Current state of the repository

> ⚠️ **This is a greenfield repository.** As of the last update to this file, the
> repo contains only `README.md` (a single title line) and this `CLAUDE.md`.
> There is **no application code, build tooling, package manifest, or
> configuration yet.**

Because of that, most of the sections below describe *intended* conventions and a
recommended starting point rather than existing structure. **When real code lands,
update this file to reflect what actually exists** — replace the "recommended"
guidance with concrete, observed facts (directory layout, commands, conventions).

## Project goal

"Japan travel website" — a website about traveling in Japan. The specific scope
(static informational site, blog, trip planner, booking/commerce, etc.) has not
been defined in the repo. Confirm the intended scope with the user before making
large architectural decisions.

## Repository layout

```
.
├── README.md      # Project title only
└── CLAUDE.md      # This file
```

Update this tree as directories and files are added.

## Getting started / development workflow

No toolchain is committed yet, so there is nothing to install, build, or run.

When scaffolding the project for the first time:

1. **Confirm the stack with the user** before choosing a framework. Don't assume.
2. Add a dependency manifest (e.g. `package.json`) and commit a lockfile.
3. Record the real commands here as soon as they exist, for example:
   - Install dependencies
   - Run the dev server
   - Build for production
   - Run tests
   - Run the linter / formatter
4. Add a `.gitignore` appropriate to the chosen stack (e.g. `node_modules/`,
   build output directories) before the first dependency install.

Until those exist, do not claim build/test/lint steps that aren't actually
present.

## Git conventions

- **Default branch:** `main`.
- **Active feature branch for this work:** `claude/claude-md-docs-ychlp6`.
  Develop on the designated feature branch; do not push directly to `main`
  without explicit permission.
- Use clear, descriptive commit messages written in the imperative mood
  (e.g. "Add homepage layout").
- Do **not** open a pull request unless the user explicitly asks for one.

## Conventions for AI assistants

- **Keep this file current.** Whenever you add structure (frameworks, directories,
  scripts, tooling, CI), update the relevant section here in the same change so the
  next assistant has an accurate picture.
- **Don't fabricate.** Do not document commands, files, or conventions that don't
  exist in the repo. Prefer "not yet defined" over an invented answer.
- **Ask before big bets.** The scope and tech stack are undecided; confirm
  direction with the user before introducing a framework or large dependency.
- Verify claims against the actual tree (the repo is small — read before you
  assert).
