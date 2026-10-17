# AGENTS.md—<App name>

Agent-facing operating notes for THIS app only. Fleet-wide rules live in the
prayticland-templates repo and the fleet plan; do not restate them here.

## App-specific facts

- <Anything an agent must know that is true only for this app: data sources,
  external accounts, unusual runtime behavior, known error states.>
- Recorded exceptions for this app (from the fleet inventory): <none / list>

## Working in this repo

- Deploys: `make deploy` (the shared deploy contract). A change is not done until it
  is committed, pushed, deployed, and `make status` is green.
- <App-specific cautions: files owned by the owner’s WIP, generated files never
  to hand-edit, test data locations.>
