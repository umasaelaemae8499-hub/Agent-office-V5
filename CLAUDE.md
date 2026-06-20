# CLAUDE.md

Guidance for AI assistants (Claude Code and others) working in this repository.

## Project status: greenfield

**This repository is currently empty.** As of the latest update it contains only
`README.md` (a single title line) and this file. There is no application code,
build tooling, package manifest, or framework chosen yet.

Treat this document as a starting contract: it records what exists today and the
conventions to follow as the codebase is built. **Keep it updated** — whenever
real structure lands (a framework is chosen, directories appear, scripts are
added), revise the relevant sections below so they describe reality rather than
intent.

## What this project is

A travel website about Japan. The intended purpose (inferred from the repository
name `Japan-travel-website`) is a site presenting Japan travel content —
destinations, guides, itineraries, and related media. Confirm specifics with the
user before assuming scope.

## Current structure

```
.
├── README.md     # Project title only
└── CLAUDE.md     # This file
```

There is no `src/`, no `package.json`, no `index.html`, and no CI configuration
yet. Do not reference files or scripts that do not exist.

## Development workflow

### Branching

- Active development branch for current work: `claude/claude-md-docs-sxb6nf`.
- The default branch is `main`.
- Create feature branches off `main`; do not commit directly to `main`.
- Never push to a branch other than the one you were assigned without explicit
  permission.

### Commits

- Use clear, descriptive commit messages explaining the "why".
- Commit logically grouped changes; keep unrelated changes in separate commits.

### Pushing

- Push with `git push -u origin <branch-name>`.
- On network failure, retry up to 4 times with exponential backoff (2s, 4s, 8s, 16s).
- Do **not** open a pull request unless the user explicitly asks for one.

## Conventions to establish

Because the stack is undecided, the first substantial change should settle these.
When you make that decision (ideally with the user), document the outcome here:

- **Framework / stack** — e.g. static HTML/CSS/JS, a static-site generator
  (Astro, Eleventy, Hugo), or a JS framework (Next.js, Vite + React/Vue). Record
  the choice and the reason.
- **Package manager** — npm / pnpm / yarn, once a JS toolchain exists.
- **Build & run commands** — add the real `dev`, `build`, `lint`, `test`, and
  `format` commands here as soon as they exist.
- **Directory layout** — document `src/`, `public/`/`static/`, `content/`, and
  asset locations once created.
- **Styling approach** — plain CSS, a preprocessor, Tailwind, or CSS modules.
- **Linting / formatting** — e.g. ESLint + Prettier; add a `.gitignore`
  (at minimum `node_modules/`, build output, and editor/OS files) before
  committing dependencies or build artifacts.

## Guidance for AI assistants

- **Don't fabricate.** If asked about build steps, tests, or architecture that
  don't exist yet, say so and offer to create them — do not invent commands.
- **Ask before choosing the stack.** Picking a framework is a significant,
  hard-to-reverse decision; confirm with the user rather than assuming.
- **Update this file** whenever the project structure or workflow changes, so it
  stays an accurate map of the codebase.
- **Keep content accurate.** Travel information (prices, schedules, opening
  hours, visa rules) goes stale; prefer linking to authoritative sources over
  hardcoding volatile facts.
