# AGENTS.md—prayticland-deploy-libs

This repository is public. Everything in it—files, code comments,
commit messages, pull requests, issues—is written for a public
audience.

## No decision references

- Never reference internal decisions in this repository: no decision
  numbers, no decision titles, no citations of internal documents or
  their sections, no dated ruling notes—in files, in code comments,
  in commit messages, anywhere.
- Decisions are not stored in this repository. Decision records are
  kept locally by the maintainers and are gitignored (see `.gitignore`).
- State the engineering reason for a change in plain prose, in the
  commit message or the documentation, without pointing at an internal
  record.

## Prose

- All prose in this repository (Markdown, documentation, code comments,
  commit messages) follows the Vale rules (vale.sh) configured in
  `.vale.ini`. Run `vale .` before committing; it must report no errors.

## Library rules

- This repo is a library. App repos vendor it as a git submodule named
  `deploy-libs/` and pin exact commits; apps must keep working from
  their pinned copy with no access to this repo at runtime.
- No deployment values in this repo, ever: no hostnames, absolute host
  paths, port maps, bucket names, or cloud project or account
  identifiers. Templates carry placeholders only; values are supplied
  at deploy time.
- Releases are git tags. There is no release flag and no release state;
  do not add flag files, dispatch machinery, or release bookkeeping.
- The deploy contract stays forward-only: no rollback path exists.
- Semver tags on this repo are the release contract for consumers.
