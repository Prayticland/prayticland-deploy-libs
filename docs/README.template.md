# <App name>

<One or two sentences: what this app is and who it serves.>

## Run / deploy

All operations go through the Prayticland deploy contract—the targets are identical in every app repo:

- `make check`—fast local gates (typecheck, unit tests, lint)
- `make preflight`—verify config dir, env file, compose config before building
- `make image`—build the image (immutable tag, OCI labels)
- `make deploy`—preflight → image → activate → loopback + public health
- `make status`—running container, labels, health state

## Configuration

Config and secrets live in the per-app directory under `~/.config/<slug>/` (never in the repo). State lives at: <state location, or "see the fleet inventory in prayticland-templates">.

Routes, ports, and versions are owned by `compose.yaml`, the Caddyfile, and `package.json`/the Dockerfile respectively—this README deliberately does not restate them (no volatile numbers in prose).
