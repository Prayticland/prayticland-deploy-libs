#!/usr/bin/env python3
"""Prayticland standards checker — deterministic, script-run.

Verifies the mechanical subset of the fleet standard against one app
repo: the Dockerfile, the Compose file, the Makefile deploy contract,
the root-document allowlist, the absence of rollback machinery,
lockfiles, and .dockerignore.

Usage: standards-check.py --repo PATH [--report-only]
Exit: 1 if any rule FAILs (always 0 with --report-only). Never auto-fixes.
Per-app parameters are read from the repo's Makefile exports (APP,
SERVICE, COMPOSE_FILE, DOCKERFILE) — the Makefile is their owner.
"""
import argparse, os, re, subprocess, sys

FAIL, PASS, SKIP = "FAIL", "PASS", "SKIP"
results = []

def report(status, rule, ref, detail=""):
    results.append((status, rule, ref, detail))

def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None

def makefile_vars(repo):
    text = read(os.path.join(repo, "Makefile")) or ""
    vars = {}
    for m in re.finditer(r"^export\s+([A-Z_]+)\s*[:?]?=\s*(.+)$", text, re.M):
        vars[m.group(1)] = m.group(2).strip()
    return vars, text

def named_block(lines, header_re):
    """Return the indented block starting at the first line matching header_re."""
    out, capture, base = [], False, None
    for ln in lines:
        m = re.match(header_re, ln)
        if m and not capture:
            capture, base = True, len(ln) - len(ln.lstrip())
            out.append(ln); continue
        if capture:
            indent = len(ln) - len(ln.lstrip())
            if ln.strip() and indent <= base:
                break
            out.append(ln)
    return "\n".join(out)

def service_block(compose_text, service):
    """Service block under `services:`, with YAML merge anchors resolved:
    if the service uses `<<: *runtime`, the anchored x- block is prepended
    (Gym and Homunculus define their shared properties that way)."""
    lines = compose_text.splitlines()
    blk = named_block(lines, rf"^\s{{2}}{re.escape(service)}:\s*$")
    m = re.search(r"<<:\s*\*(\S+)", blk)
    if m:
        anchor = named_block(lines, rf"^x-\S+:\s*&{re.escape(m.group(1))}\s*$")
        blk = anchor + "\n" + blk
    return blk

# Recorded per-app exceptions: root-level data files that are not docs.
ROOT_DOC_EXCEPTIONS = {
    "gym-routine-tracker": {"WORKOUT_ROUTINE.md", "WORKOUT_CONTEXT.md"},  # both bind-mounted app data
    "stamps": {"SOURCES.md", "LEGAL_EXPORT_RU.md"},
    "lifegame": set(), "networker": set(), "uscis-case-watcher": set(),
    "homunculus-icons": set(), "gallery": set(),
}
ROOT_DOC_ALLOWLIST = {"README.md", "AGENTS.md"}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()
    repo = args.repo
    vars, mk = makefile_vars(repo)
    app = vars.get("APP_KEY") or vars.get("APP") or os.path.basename(os.path.abspath(repo))
    project = vars.get("APP", app)
    service = vars.get("SERVICE", "app")
    compose_rel = vars.get("COMPOSE_FILE", "compose.yaml").replace("$(APP_CONFIG_DIR)", "")
    dockerfile_rel = vars.get("DOCKERFILE", "Dockerfile")

    # --- Dockerfile ---
    df = read(os.path.join(repo, dockerfile_rel))
    if df is None:
        report(FAIL, "dockerfile-exists", "", f"{dockerfile_rel} not found")
    else:
        report(PASS, "dockerfile-exists", "", dockerfile_rel)
        froms = re.findall(r"^FROM\s+(\S+)", df, re.M)
        stages = set(re.findall(r"^FROM\s+\S+(?:\s+[Aa][Ss]\s+(\S+))?", df, re.M))
        unpinned = [f for f in froms if "@sha256:" not in f and f not in stages and f != "scratch"]
        if unpinned:
            report(FAIL, "base-images-digest-pinned", "", f"unpinned: {', '.join(unpinned)}")
        else:
            report(PASS, "base-images-digest-pinned", "", f"{len(froms)} FROM line(s)")
        pkg = read(os.path.join(repo, "package.json")) or ""
        if '"build"' in pkg:
            if len(froms) >= 2:
                report(PASS, "multistage-build", "", f"{len(froms)} stages")
            else:
                report(FAIL, "multistage-build", "", "compiled frontend but single-stage Dockerfile")

    # --- Compose ---
    cx = read(os.path.join(repo, compose_rel))
    if cx is None:
        report(FAIL, "compose-exists", "", f"{compose_rel} not found")
    else:
        name_m = re.search(r"^name:\s*(\S+)", cx, re.M)
        if name_m and name_m.group(1) == project:
            report(PASS, "compose-project-name", "", name_m.group(1))
        else:
            report(FAIL, "compose-project-name", "", f"name: {name_m.group(1) if name_m else 'missing'} != {project}")
        blk = service_block(cx, service)
        if not blk:
            report(FAIL, "compose-service-found", "", f"service {service} not in {compose_rel}")
        else:
            def has(pat):
                return re.search(pat, blk) is not None
            checks = [
                ("compose-read-only", r"read_only:\s*true"),
                ("compose-cap-drop-all", r"cap_drop:\s*\[ALL\]"),
                ("compose-no-new-privileges", r"no-new-privileges"),
                ("compose-init", r"init:\s*true"),
                ("compose-restart", r"restart:\s*unless-stopped"),
            ]
            for rule, pat in checks:
                report(PASS if has(pat) else FAIL, rule, "", "" if has(pat) else f"missing in service {service}")
            # Healthcheck: in Compose, or baked into the image's
            # Dockerfile HEALTHCHECK (Gym's pattern) — either satisfies it.
            if has(r"healthcheck:"):
                report(PASS, "compose-healthcheck", "", "in compose")
            elif df and "HEALTHCHECK" in df:
                report(PASS, "compose-healthcheck", "", "via Dockerfile HEALTHCHECK")
            else:
                report(FAIL, "compose-healthcheck", "", f"missing in service {service} and Dockerfile")
            if has(r"driver:\s*local"):
                report(PASS, "compose-logging-local", "")
            else:
                report(FAIL, "compose-logging-local", "", "logging driver is not 'local'")
            if re.search(r"^\s+user:", blk, re.M):
                report(PASS, "compose-explicit-user", "")
            else:
                report(FAIL, "compose-explicit-user", "", "no explicit user: in primary service")
            img = re.search(r"image:\s*(\S+)", blk)
            if img and "${" in img.group(1):
                report(PASS, "compose-image-immutable", "", img.group(1))
            else:
                report(FAIL, "compose-image-immutable", "", f"image: {img.group(1) if img else 'missing'} is not env-interpolated")
            bad_ports = [ip for ip in re.findall(r'["\[]\s*"?(\d+\.\d+\.\d+\.\d+):\d+:\d+', blk) if ip != "127.0.0.1"]
            bare = re.findall(r'-\s*"?(\d+:\d+)"?\s*$', blk, re.M)
            if bad_ports or bare:
                report(FAIL, "compose-ports-loopback", "", f"non-loopback bindings: {bad_ports + bare}")
            else:
                report(PASS, "compose-ports-loopback", "")

    # --- Makefile contract  ---
    targets = set(re.findall(r"^([a-z][a-z-]*):", mk, re.M))
    missing = [t for t in ("check", "preflight", "image", "deploy", "status") if t not in targets]
    if missing:
        report(FAIL, "makefile-contract", "", f"missing targets: {', '.join(missing)}")
    elif "deploy.sh" in mk:
        report(PASS, "makefile-contract", "", "five targets, delegates to deploy.sh")
    else:
        report(FAIL, "makefile-contract", "", "targets present but deploy.sh not referenced")

    # --- Rollback machinery : actual machinery only — a rollback()
    # function, a rollback trap, or a rollback script file. Prose mentions
    # (e.g. "no rollback exists here") are not machinery and must not flag.
    offenders = []
    for root, _dirs, files in os.walk(repo):
        if ".git" in root:
            continue
        for fn in files:
            if "rollback" in fn.lower():
                offenders.append(os.path.relpath(os.path.join(root, fn), repo))
                continue
            if fn == "Makefile" or "deploy" in fn:
                t = read(os.path.join(root, fn)) or ""
                if re.search(r"rollback\s*\(\s*\)", t) or re.search(r"trap\s+.*rollback", t, re.I):
                    offenders.append(os.path.relpath(os.path.join(root, fn), repo))
    if offenders:
        report(FAIL, "no-rollback-machinery", "", f"found in: {', '.join(offenders)}")
    else:
        report(PASS, "no-rollback-machinery", "")

    # --- Root docs  ---
    root_md = {f for f in os.listdir(repo) if f.endswith(".md") and os.path.isfile(os.path.join(repo, f))}
    allowed = ROOT_DOC_ALLOWLIST | ROOT_DOC_EXCEPTIONS.get(app, set())
    extra = sorted(root_md - allowed)
    if extra:
        report(FAIL, "root-doc-allowlist", "", f"non-allowlisted root docs: {', '.join(extra)}")
    else:
        report(PASS, "root-doc-allowlist", "")

    # --- Lockfiles  ---
    if os.path.exists(os.path.join(repo, "package.json")):
        ok = os.path.exists(os.path.join(repo, "package-lock.json"))
        report(PASS if ok else FAIL, "lockfile", "", "package-lock.json" if ok else "package-lock.json missing")
    if os.path.exists(os.path.join(repo, "pyproject.toml")):
        ok = os.path.exists(os.path.join(repo, "uv.lock"))
        report(PASS if ok else FAIL, "lockfile", "", "uv.lock" if ok else "uv.lock missing")

    # --- Hex literals in UI sources : zero in app sources; the palette
    # lives in prayticland-ui-libs tokens. Scans the frontend source root.
    ui_roots = [os.path.join(repo, cand) for cand in ("src", "web/src", "css")
                if os.path.isdir(os.path.join(repo, cand))]
    if ui_roots:
        # Hexes inside a CSS custom-property DEFINITION line (`--x: #hex;`)
        # are the app's recorded local-token supplement;
        # the per-app allowance caps them. Every other hex fails.
        HEX_DEF_ALLOWANCE = {"gym-routine-tracker": 1, "stamps": 9}
        allowance = HEX_DEF_ALLOWANCE.get(app, 0)
        hex_files = {}
        def_hexes = 0
        for ui_root in ui_roots:
          for dirpath, dirnames, filenames in os.walk(ui_root):
            dirnames[:] = [d for d in dirnames if d not in ("node_modules", "dist")]
            for fn in filenames:
                if fn.endswith((".ts", ".tsx", ".js", ".jsx", ".css")):
                    t = read(os.path.join(dirpath, fn)) or ""
                    n = 0
                    for line in t.splitlines():
                        found = len(re.findall(r"#[0-9a-fA-F]{3,8}\b", line))
                        if not found:
                            continue
                        if fn.endswith(".css"):
                            defs = len(re.findall(r"--[\w-]+\s*:\s*#[0-9a-fA-F]{3,8}\b", line))
                            def_hexes += defs
                            n += found - defs
                        else:
                            n += found
                    if n:
                        hex_files[os.path.relpath(os.path.join(dirpath, fn), repo)] = n
        if hex_files or def_hexes > allowance:
            parts = []
            if hex_files:
                top = ", ".join(f"{f}:{n}" for f, n in sorted(hex_files.items(), key=lambda kv: -kv[1])[:3])
                parts.append(f"{sum(hex_files.values())} hex literals ({top})")
            if def_hexes > allowance:
                parts.append(f"{def_hexes} local token-definition hexes > allowance {allowance}")
            report(FAIL, "hex-literals-zero", "", "; ".join(parts))
        else:
            report(PASS, "hex-literals-zero", "",
                   f"{def_hexes} local token definitions" if def_hexes else "")

    # --- .dockerignore : either explicit patterns, or the allowlist
    # style (a leading `**` blanket ignore with negations — Networker).
    di = read(os.path.join(repo, ".dockerignore"))
    if di is None:
        report(FAIL, "dockerignore", "", "missing")
    elif ("node_modules" in di and ".git" in di) or any(ln.strip() in ("*", "**") for ln in di.splitlines()):
        report(PASS, "dockerignore", "")
    else:
        report(FAIL, "dockerignore", "", "must cover at least node_modules and .git")

    fails = [r for r in results if r[0] == FAIL]
    for status, rule, ref, detail in results:
        print(f"[{status}] {app} {rule}{': ' + detail if detail else ''}")
    print(f"{app}: {len(results) - len(fails)} passed, {len(fails)} failed")
    return 0 if (args.report_only or not fails) else 1

if __name__ == "__main__":
    sys.exit(main())
