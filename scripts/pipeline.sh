#!/usr/bin/env bash
# One cycle of the tei pipeline (docs/PIPELINE.md): collect the readings,
# check them, and publish them through one rolling data pull request that
# this script merges itself once the checks pass (decision B, 13.11.2026).
#
#   scripts/pipeline.sh             a normal cycle (the systemd timer)
#   scripts/pipeline.sh --dry-run   collect and check, publish nothing
#
# The checkout (~/sg-missiven-data) belongs to the pipeline: each cycle resets
# it to origin/main. Code never reaches main this way; only readings/,
# entities/ and regests/ may change, or the cycle stops.
set -euo pipefail

REPO=${SGM_REPO:-$HOME/sg-missiven-data}
PY=${SGM_PY:-$HOME/sg-missiven-venv/bin/python}
STATE=${SGM_STATE:-$HOME/sgm-pipeline}
GH_REPO=DHBern/sg-missiven-data
BRANCH=data/auto
DATA_PATHS=(readings entities regests)
DRY=${1:-}

mkdir -p "$STATE"
exec 9>"$STATE/lock"
flock -n 9 || { echo "another cycle is running"; exit 0; }

now() { date -Is; }
status() {  # status <state> <message> [pr]
  printf '{"at":"%s","state":"%s","message":%s,"pr":"%s"}\n' "$(now)" "$1" \
    "$(printf '%s' "$2" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')" "${3:-}" \
    >> "$STATE/status.jsonl"
  echo "[$1] $2"
}
fail() { status failed "$1"; exit 1; }
# any other error (set -e) is recorded too, not only the ones checked below
trap 'status failed "line $LINENO: $BASH_COMMAND"' ERR

cd "$REPO"
git fetch -q origin
git switch -q main 2>/dev/null || git switch -q -c main origin/main
git reset -q --hard origin/main
git clean -q -fd -- "${DATA_PATHS[@]}" 2>/dev/null || true

# 1. collect (merges; exits non-zero if a reader would lose pages)
python3 scripts/collect_series.py > "$STATE/collect.log" 2>&1 || fail "collect: $(tail -1 "$STATE/collect.log")"

changed=$(git status --porcelain -- "${DATA_PATHS[@]}")
if [ -z "$changed" ]; then
  status idle "nothing new"
  exit 0
fi

# 2. guards: nothing outside the data paths, and the site must build
other=$(git status --porcelain | grep -v -E "^.. ($(IFS='|'; echo "${DATA_PATHS[*]}"))/" || true)
[ -z "$other" ] || fail "changes outside the data paths: $other"
"$PY" scripts/build_site.py > "$STATE/build.log" 2>&1 || fail "build_site.py failed: $(tail -1 "$STATE/build.log")"

summary=$(grep -E ' -> .*pages' "$STATE/collect.log" | grep -v '(+0 new, 0 changed)' || true)
letters=$(grep -m1 -E '^[0-9]+ letters' "$STATE/build.log" || true)

if [ "$DRY" = "--dry-run" ]; then
  status dry-run "would publish: ${summary//$'\n'/; } | $letters"
  git reset -q --hard origin/main
  exit 0
fi

# 3. publish: one rolling branch, one PR, merged here when the checks passed
git switch -q -C "$BRANCH"
existing=(); for p in "${DATA_PATHS[@]}"; do [ -e "$p" ] && existing+=("$p"); done
git add -A -- "${existing[@]}"
git commit -q -F - <<EOF
readings: automatic collection on tei, $(date '+%d.%m.%Y %H:%M')

$summary

$letters
EOF
git push -q -f origin "$BRANCH"

pr=$(gh pr list -R "$GH_REPO" --head "$BRANCH" --state open --json number --jq '.[0].number // empty')
body="Automatic collection by the tei pipeline (scripts/pipeline.sh, docs/PIPELINE.md).

\`\`\`
$summary
$letters
\`\`\`

Checks passed: collect merged without losing pages; only ${DATA_PATHS[*]} changed; build_site.py ran. The pipeline merges this PR itself."
if [ -z "$pr" ]; then
  pr=$(gh pr create -R "$GH_REPO" --base main --head "$BRANCH" \
        --title "Readings update (automatic, tei)" --body "$body" | grep -oE '[0-9]+$')
else
  gh pr edit "$pr" -R "$GH_REPO" --body "$body" >/dev/null
fi
gh pr merge "$pr" -R "$GH_REPO" --merge --delete-branch >/dev/null || fail "merge of #$pr failed"
git switch -q main && git reset -q --hard origin/main
status published "${summary//$'\n'/; } | $letters" "$pr"
