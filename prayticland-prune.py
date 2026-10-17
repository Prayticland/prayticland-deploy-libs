#!/usr/bin/env python3
"""prayticland-prune — standing Docker hygiene for the Prayticland fleet.

Policy: per fleet app repo keep the running image
plus the N most recent tags (N below is the ONLY place this number lives);
USCIS repos keep timestamped tags only (local/before-*/previous snapshots are
removed even inside the window); legacy experiment repos are removed whole.
Base images, platform services, and tooling images are never touched.
Hard guard: an image referenced by ANY container (running or stopped) is
never removed. Default mode is DRY RUN; pass --apply to delete.
Every run writes a full log under ~/.local/state/prayticland-prune/."""
import subprocess, re, sys, os, datetime
from collections import defaultdict

N = 3
CACHE_CAP = '5GB'
FLEET = {'networker', 'lifegame-tailnet', 'gym-routine-tracker', 'uscis-case-watcher-app',
         'uscis-case-watcher-worker', 'homunculus-icons'}
LEGACY_WHOLE = {'prayticland', 'hmf-deploy-baseline'}
TS = re.compile(r'^\d{8}-\d{6}-')
APPLY = '--apply' in sys.argv

def out(cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()

LOG = []
def log(s):
    LOG.append(s); print(s, flush=True)

prot = set()
for line in out(['docker', 'ps', '-a', '--format', '{{.Image}}']).splitlines():
    if not line: continue
    r = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', line],
                       capture_output=True, text=True)
    if r.returncode == 0: prot.add(r.stdout.strip())

rows, by, seen = [], defaultdict(list), set()
for line in out(['docker', 'images', '--format', '{{.Repository}}|{{.Tag}}|{{.ID}}|{{.CreatedAt}}', '--no-trunc']).splitlines():
    p = line.split('|')
    if len(p) == 4 and (p[0], p[1]) not in seen:
        seen.add((p[0], p[1])); by[p[0]].append((p[1], p[2], p[3]))
for repo in by: by[repo].sort(key=lambda x: x[2], reverse=True)

running = {}
for line in out(['docker', 'ps', '--format', '{{.Image}}']).splitlines():
    if ':' in line:
        repo, tag = line.rsplit(':', 1); running[repo] = tag

remove = []
for repo, imgs in sorted(by.items()):
    if repo in FLEET:
        cand = [t for t, _, _ in imgs if TS.match(t)] if repo.startswith('uscis-') else [t for t, _, _ in imgs]
        keep_tags = set(cand[:N])
        if repo in running: keep_tags.add(running[repo])
        remove += [(repo, t, i) for t, i, _ in imgs if t not in keep_tags]
    elif repo in LEGACY_WHOLE:
        remove += [(repo, t, i) for t, i, _ in imgs]
    elif repo == 'mcr.microsoft.com/playwright':
        newest = imgs[0][0]
        remove += [(repo, t, i) for t, i, _ in imgs if t != newest]

log(f'{"APPLY" if APPLY else "DRY RUN"}: {len(remove)} tags slated for removal')
removed = failed = skipped = 0
for repo, tag, iid in remove:
    if iid in prot:
        log(f'SKIP (container-referenced): {repo}:{tag}'); skipped += 1; continue
    if not APPLY:
        log(f'WOULD REMOVE {repo}:{tag}'); continue
    r = subprocess.run(['docker', 'rmi', f'{repo}:{tag}'], capture_output=True, text=True)
    if r.returncode == 0: log(f'REMOVED {repo}:{tag}'); removed += 1
    else: log(f'FAILED  {repo}:{tag} :: {(r.stderr or r.stdout).strip()[:160]}'); failed += 1

if APPLY:
    r = subprocess.run(['docker', 'image', 'prune', '-f'], capture_output=True, text=True)
    log('dangling prune: ' + (r.stdout.strip().splitlines() or [''])[-1])
    r = subprocess.run(['docker', 'builder', 'prune', '-f', '--keep-storage', CACHE_CAP],
                       capture_output=True, text=True)
    if r.returncode != 0:
        r = subprocess.run(['docker', 'builder', 'prune', '-f'], capture_output=True, text=True)
    log('builder prune: ' + (r.stdout.strip().splitlines() or [''])[-1])
    log(f'SUMMARY removed={removed} failed={failed} skipped={skipped}')
    log(out(['docker', 'system', 'df']))

stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
logdir = os.path.expanduser('~/.local/state/prayticland-prune')
os.makedirs(logdir, exist_ok=True)
path = os.path.join(logdir, f'prune-{stamp}-{"apply" if APPLY else "dryrun"}.log')
open(path, 'w').write('\n'.join(LOG) + '\n')
print(f'log: {path}')
