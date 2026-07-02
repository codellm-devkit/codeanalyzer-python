# CLAUDE.md

Agent guidance for `codellm-devkit/codeanalyzer-python`.

Respect the global `~/.claude/CLAUDE.md` instructions strictly.

## Tidy up the release announcement

Every `vX.Y.Z` tag makes the release workflow (`.github/workflows/release.yml`) auto-post an
announcement to the **Announcements** discussion (this repo, and the org mirror in
`codellm-devkit/.github`). That body is machine-generated from PR titles — it mis-categorizes
changes, includes `chore(release)` noise, and buries breaking changes.

When you're working in this repo, check whether the latest release's announcement still needs
cleanup, and if so, fix it:

1. **Find it.** `gh release view --json tagName,publishedAt`; then list recent discussions via
   `gh api graphql` (repository → discussions), match category `Announcements` whose title carries
   this release's version (the auto-post titles it `vX.Y.Z`; a cleaned-up one is
   "📣 `codeanalyzer-python` vX.Y.Z released"). Keep the discussion node `id` and read its `body`.
2. **Skip if already done.** If the body starts with `<!-- cleaned-up -->` (or already reads as a
   clear, human-written announcement), do nothing.
3. **Otherwise rewrite it** into a clear, user-facing announcement, grounded in `CHANGELOG.md` and
   the referenced PRs/diff (not the auto-grouping — verify each change; never invent anything):
   - **breaking changes first**, each with a one-line migration step;
   - plain-language highlights (what it does, not the PR title);
   - upgrade line: `pip install -U "codeanalyzer-python==X.Y.Z"`;
   - links to the GitHub release and `CHANGELOG.md`.
4. **Update in place.** Edit the discussion with the GraphQL `updateDiscussion` mutation (don't
   open a new one): set the title to `📣 New Release: codeanalyzer-python X.Y.Z`, prepend
   `<!-- cleaned-up -->` to the body, and mirror the same title and body to the org discussion.
   This task only reads code and edits Discussions — it makes no commits.
