#!/usr/bin/env bash
# prayticland-deploy-libs deploy.sh — the shared Prayticland deploy
# implementation, vendored into each app repo as the deploy-libs
# module. ONE implementation for every app.
# ONE implementation for every app; per-app Makefiles set variables and call:
#   deploy.sh preflight | image | activate | deploy | status
#
# Required env (set by the app's Makefile):
#   APP                 app slug = compose project name = image repository name
#   SERVICE             primary compose service (default: app)
#   APP_CONFIG_DIR      per-app config directory (~/.config/<app>); "none" if the app has none
#   LOOPBACK_HEALTH_URL health endpoint on 127.0.0.1
#   PUBLIC_HEALTH_URL   public health endpoint (value supplied via deploy.env)
# Optional env:
#   ENV_FILE            compose --env-file (default: $APP_CONFIG_DIR/runtime.env; "none" skips)
#   COMPOSE_FILE        default: compose.yaml
#   DOCKERFILE          default: Dockerfile
#   APP_ENV_PREFIX      legacy env prefix (e.g. NETWORKER): the script also exports
#                       <PREFIX>_IMAGE/_COMMIT/_VERSION/_RELEASE/_CONFIG_DIR aliases so
#                       existing compose files keep working during migration.
#   IMAGE_REPOSITORY    image repo if it differs from APP (e.g. uscis-case-watcher-app)
#   SECOND_IMAGE_REPOSITORY / SECOND_DOCKERFILE / SECOND_IMAGE_VAR
#                       optional second image built with the same tag (multi-image repos)
#   WAIT_TIMEOUT        compose --wait timeout seconds (default: 120)
#   TEMPLATES_DIR       the deploy-libs root (default: this script's dir)
#   DEPLOY_LOG          default: ~/.local/state/prayticland/deploys.jsonl
#
# Forward-only: on any failure the script stops, leaves the failed state in
# place, records the failure in the deploy log, and reports. No rollback exists here.
set -eEuo pipefail   # -E: the ERR trap (deploy-log failure records) must inherit into functions

APP="${APP:?APP is required}"
SERVICE="${SERVICE:-app}"
APP_CONFIG_DIR="${APP_CONFIG_DIR:-none}"   # "none" for apps without a config dir
LOOPBACK_HEALTH_URL="${LOOPBACK_HEALTH_URL:?LOOPBACK_HEALTH_URL is required}"
PUBLIC_HEALTH_URL="${PUBLIC_HEALTH_URL:?PUBLIC_HEALTH_URL is required}"
if [ "$APP_CONFIG_DIR" = "none" ]; then
  ENV_FILE="${ENV_FILE:-none}"
else
  ENV_FILE="${ENV_FILE-$APP_CONFIG_DIR/runtime.env}"
fi
COMPOSE_FILE="${COMPOSE_FILE:-compose.yaml}"
DOCKERFILE="${DOCKERFILE:-Dockerfile}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-120}"
TEMPLATES_DIR="${TEMPLATES_DIR:-$(cd "$(dirname "$0")" && pwd)}"
DEPLOY_LOG="${DEPLOY_LOG:-$HOME/.local/state/prayticland/deploys.jsonl}"
START_TS=$SECONDS
STEP="init"

fail() { echo "ERROR: $*" >&2; exit 1; }

# --- identity: commit, worktree hash, immutable tag (fleet de-facto format) ---
COMMIT="$(git rev-parse HEAD)"
WORKTREE_HASH="$( (git diff --binary HEAD; git ls-files --others --exclude-standard -z | sort -z | xargs -0 -r sha256sum) | sha256sum | cut -c1-12 )"
if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
  REVISION="${COMMIT}-worktree.${WORKTREE_HASH}"
else
  REVISION="$COMMIT"
fi
IMAGE_TAG="${IMAGE_TAG:-$(date -u +%Y%m%d-%H%M%S)-${WORKTREE_HASH}}"
VERSION="$(git describe --tags --always --dirty 2>/dev/null || echo "$COMMIT")"
IMAGE_REPOSITORY="${IMAGE_REPOSITORY:-$APP}"
APP_IMAGE="${IMAGE_REPOSITORY}:${IMAGE_TAG}"
# Optional second image (multi-image repos, e.g. Stamps app + ops sidecar):
# SECOND_IMAGE_REPOSITORY + SECOND_DOCKERFILE + SECOND_IMAGE_VAR (compose env name).
SECOND_IMAGE=""
if [ -n "${SECOND_IMAGE_REPOSITORY:-}" ]; then
  SECOND_IMAGE="${SECOND_IMAGE_REPOSITORY}:${IMAGE_TAG}"
fi

# --- version identity: there is no release flag. Releases are
# git tags; VERSION (git describe, above) is the identity this deployment
# carries. ubuntu-server is the dev plane; prod is Telegram Serverless. ---
export APP_IMAGE APP_REVISION="$REVISION" APP_VERSION="$VERSION" APP_CONFIG_DIR
if [ -n "${SECOND_IMAGE:-}" ]; then
  export "${SECOND_IMAGE_VAR:-APP_OPS_IMAGE}=$SECOND_IMAGE"
fi
if [ -n "${APP_ENV_PREFIX:-}" ]; then
  export "${APP_ENV_PREFIX}_IMAGE=$APP_IMAGE" "${APP_ENV_PREFIX}_IMAGE_TAG=$IMAGE_TAG" \
         "${APP_ENV_PREFIX}_COMMIT=$REVISION" \
         "${APP_ENV_PREFIX}_VERSION=$VERSION" "${APP_ENV_PREFIX}_RELEASE=$IMAGE_TAG" \
         "${APP_ENV_PREFIX}_CONFIG_DIR=$APP_CONFIG_DIR"
fi

compose() {
  if [ "$ENV_FILE" = "none" ]; then
    docker compose --project-name "$APP" --file "$COMPOSE_FILE" "$@"
  else
    docker compose --project-name "$APP" --env-file "$ENV_FILE" --file "$COMPOSE_FILE" "$@"
  fi
}

DEPLOY_LOGGED=0
log_deploy() { # outcome loopback public
  mkdir -p "$(dirname "$DEPLOY_LOG")"
  printf '{"ts":"%s","app":"%s","commit":"%s","revision":"%s","image":"%s","outcome":"%s","loopback":"%s","public":"%s","duration_s":%s}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$APP" "$COMMIT" "$REVISION" "$APP_IMAGE" "$1" "$2" "$3" \
    "$((SECONDS - START_TS))" >> "$DEPLOY_LOG"
  DEPLOY_LOGGED=1
}
# EVERY deploy attempt emits a record — including failures before the
# activation checks (build errors, compose --wait timeouts). DEPLOY_ATTEMPT is
# set only by the image/activate/deploy branches of the dispatcher below.
trap '[ "${DEPLOY_ATTEMPT:-0}" = 1 ] && [ "$DEPLOY_LOGGED" = 0 ] && log_deploy failure "" "" || true' ERR

cmd_preflight() {
  # The app configures itself with prayticland.yaml in its repo root
  # — refuse to deploy when it names a different app.
  if [ -f prayticland.yaml ]; then
    local _yaml_app
    _yaml_app="$(sed -n 's/^app:[[:space:]]*//p' prayticland.yaml | head -n1 | tr -d ' \t\r')"
    if [ -n "$_yaml_app" ] && [ "$_yaml_app" != "$APP" ]; then
      echo "preflight: prayticland.yaml app '$_yaml_app' does not match APP '$APP'" >&2
      return 1
    fi
  fi

  # Deployment values come from the app config dir's deploy.env
  # — fail closed, naming what is missing.
  local _missing=0
  for _var in LOOPBACK_HEALTH_URL PUBLIC_HEALTH_URL; do
    if [ -z "${!_var:-}" ]; then
      echo "preflight: $_var is not set — deployment values live in $APP_CONFIG_DIR/deploy.env, not in the repo" >&2
      _missing=1
    fi
  done
  [ "$_missing" -eq 0 ] || return 1

  STEP=preflight
  command -v docker >/dev/null || fail "docker not found"
  command -v curl >/dev/null || fail "curl not found"
  if [ "$APP_CONFIG_DIR" != "none" ]; then
    [ -d "$APP_CONFIG_DIR" ] || fail "config dir missing: $APP_CONFIG_DIR"
    [ "$(stat -c '%a' "$APP_CONFIG_DIR")" = "700" ] || fail "config dir perms must be 700: $APP_CONFIG_DIR"
  fi
  if [ "$ENV_FILE" != "none" ]; then
    [ -f "$ENV_FILE" ] || fail "env file missing: $ENV_FILE"
    [ "$(stat -c '%a' "$ENV_FILE")" = "600" ] || fail "env file perms must be 600: $ENV_FILE"
  fi
  [ -n "$COMMIT" ] || fail "not a git checkout (commit is empty)"
  compose config --quiet || fail "compose config invalid"
  printf 'Preflight ok: %s (%s)\n' "$APP_IMAGE" "$REVISION"
}

cmd_image() {
  STEP=image
  local build_args=()
  if [ "${BUILD_APP_ARGS:-}" = "1" ]; then
    # Apps whose Dockerfile stamps version/commit at build time (Lifegame).
    build_args+=(--build-arg "APP_VERSION=$VERSION" --build-arg "APP_COMMIT=$REVISION")
  fi
  docker build \
    --label "org.opencontainers.image.version=$VERSION" \
    --label "org.opencontainers.image.revision=$REVISION" \
    --label "org.opencontainers.image.created=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --label "land.praytic.version=$VERSION" \
    "${build_args[@]}" \
    --tag "$APP_IMAGE" \
    --file "$DOCKERFILE" .
  printf 'Built %s\n' "$APP_IMAGE"
  if [ -n "${SECOND_IMAGE:-}" ]; then
    docker build \
      --label "org.opencontainers.image.version=$VERSION" \
      --label "org.opencontainers.image.revision=$REVISION" \
      --label "org.opencontainers.image.created=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      --label "land.praytic.version=$VERSION" \
      --tag "$SECOND_IMAGE" \
      --file "${SECOND_DOCKERFILE:-Dockerfile.ops}" .
    printf 'Built %s\n' "$SECOND_IMAGE"
  fi
}

poll_loopback() {
  local i
  for i in $(seq 1 30); do
    if curl --fail --silent --max-time 5 "$LOOPBACK_HEALTH_URL" >/dev/null 2>&1; then return 0; fi
    sleep 2
  done
  return 1
}

cmd_activate() {
  STEP=activate
  compose up --detach --remove-orphans --wait --wait-timeout "$WAIT_TIMEOUT" --no-build
  local cid
  cid="$(docker ps -q --filter "label=com.docker.compose.project=$APP" --filter "label=com.docker.compose.service=$SERVICE" | head -n1)"
  [ -n "$cid" ] || { log_deploy failure "" ""; fail "no running container for service $SERVICE"; }
  [ "$(docker inspect --format '{{.Config.Image}}' "$cid")" = "$APP_IMAGE" ] \
    || { log_deploy failure "" ""; fail "container image mismatch (expected $APP_IMAGE)"; }
  [ "$(docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$cid")" = "$REVISION" ] \
    || { log_deploy failure "" ""; fail "container revision label mismatch"; }
  poll_loopback || { log_deploy failure fail ""; fail "loopback health failed: $LOOPBACK_HEALTH_URL"; }
  curl --fail --silent --max-time 20 "$PUBLIC_HEALTH_URL" >/dev/null \
    || { log_deploy failure ok fail; fail "public health failed: $PUBLIC_HEALTH_URL"; }
  log_deploy success ok ok
  printf 'Deployed %s\n' "$APP_IMAGE"
}

cmd_status() {
  printf '%s: DEV (ubuntu-server)\n' "$APP"
  docker ps --filter "label=com.docker.compose.project=$APP" \
    --format '{{.Image}} | {{.Status}} | {{.Names}}'
  local cid
  cid="$(docker ps -q --filter "label=com.docker.compose.project=$APP" --filter "label=com.docker.compose.service=$SERVICE" | head -n1)"
  if [ -n "$cid" ]; then
    printf 'revision: %s\n' "$(docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$cid")"
    printf 'version: %s\n' "$(docker inspect --format '{{index .Config.Labels "land.praytic.version"}}' "$cid")"
  fi
  curl --fail --silent --max-time 20 "$LOOPBACK_HEALTH_URL" >/dev/null && echo "loopback health: ok" || { echo "loopback health: FAIL"; return 1; }
  curl --fail --silent --max-time 20 "$PUBLIC_HEALTH_URL" >/dev/null && echo "public health: ok" || { echo "public health: FAIL"; return 1; }
}

case "${1:?usage: deploy.sh preflight|image|activate|deploy|status}" in
  preflight) cmd_preflight ;;
  image)     DEPLOY_ATTEMPT=1; cmd_preflight; cmd_image ;;
  activate)  DEPLOY_ATTEMPT=1; cmd_activate ;;
  deploy)    DEPLOY_ATTEMPT=1; cmd_preflight; cmd_image; cmd_activate ;;
  status)    cmd_status ;;
  *) fail "unknown command: $1" ;;
esac
