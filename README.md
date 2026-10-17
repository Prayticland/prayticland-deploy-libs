# prayticland-deploy-libs

The Prayticland deployment library. Every app
repo vendors this library as a git submodule at `deploy-libs/` and
configures itself with a `prayticland.yaml` in its repo root (see
`prayticland.example.yaml`). The library contains everything
deploy-related that used to live in the central prayticland-templates
repo: `deploy.sh` (the shared deploy contract), `standards-check.py`,
`prayticland-prune.py` (image retention), `Makefile.template`, the
templated Dockerfiles under `docker/`, and the doc templates under
`docs/`.

## The two planes

- **Dev** — every commit deploys to the fleet's dev server via
  `make deploy` (this library's `deploy.sh`); dev versions are reachable
  only on the private network.
- **Prod** — releases are **git tags**. A version tag is deployed to
  Telegram Serverless (tgcloud) and is reachable only via its
  `app<app_id>.tgcloud.ai` domain. There is no release flag.

## Rules

- Deployment values are never stored in this library or in any app
  repo: no hostnames, host paths, port maps, bucket names, or cloud
  identifiers. Apps receive values at deploy time —
  dev from the host config dir's `deploy.env`, prod from the tgcloud
  project configuration.
- Changes land here once and reach apps by bumping the submodule pin;
  apps pin exact library commits (semver tags on this repo).
- `deploy.sh` stays forward-only: no rollback path exists.
