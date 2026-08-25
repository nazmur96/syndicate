#!/usr/bin/env bash
#
# Install the syndicate caller workflow into one of your repositories.
#
#   scripts/install-into.sh OWNER/REPO [--token-check]
#
# Does what the manual route makes you do by hand:
#   1. writes .github/workflows/syndicate.yml, with the owner and the repo's
#      actual default branch filled in (not every repo is on `main`);
#   2. adds a CLAUDE.md section describing the per-post workflow, so an agent
#      working in that repo knows the rules without being told;
#   3. copies DEVTO_API_KEY / LINKEDIN_ACCESS_TOKEN / LINKEDIN_PERSON_URN from
#      your local .env into that repo's Actions secrets, and
#      LINKEDIN_TOKEN_ISSUED_AT into its Actions variables;
#   4. with --token-check, also installs the weekly expiry reminder. Install
#      that in ONE repo only -- the token is the same everywhere, so N copies
#      raise N identical issues for a single re-authorisation.
#
# Installing costs nothing at rest: a document is syndicated only if it carries
# a `crosspost:` frontmatter block, so a repo with the workflow and no opted-in
# documents does nothing on every push.
#
# NOTE ON SECRETS: this writes the token into each repo separately, which is
# what repository-scoped secrets mean. When LinkedIn's 60-day token expires you
# must re-run this for every repo. Organisation secrets would make that one
# update; see docs/SETUP.md.

set -euo pipefail

REPO="${1:-}"
WANT_TOKEN_CHECK="${2:-}"

if [ -z "$REPO" ] || [[ "$REPO" != */* ]]; then
  echo "usage: $0 OWNER/REPO [--token-check]" >&2
  exit 64
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OWNER="${REPO%%/*}"
ENV_FILE="$HERE/.env"

[ -f "$ENV_FILE" ] || { echo "error: no .env at $ENV_FILE -- nothing to copy." >&2; exit 66; }

command -v gh >/dev/null || { echo "error: the gh CLI is required." >&2; exit 69; }
gh auth status >/dev/null 2>&1 || { echo "error: gh is not authenticated; run 'gh auth login'." >&2; exit 77; }

# A private tool repo cannot be called by a public caller, and the reverse
# leaks nothing -- so check visibility rather than let Actions fail at runtime.
if [ "$(gh repo view "$OWNER/syndicate" --json isPrivate --jq .isPrivate 2>/dev/null)" = "true" ] \
   && [ "$(gh repo view "$REPO" --json isPrivate --jq .isPrivate)" = "false" ]; then
  echo "error: $OWNER/syndicate is private but $REPO is public;" >&2
  echo "       a public repo cannot call a private reusable workflow." >&2
  exit 65
fi

BRANCH="$(gh repo view "$REPO" --json defaultBranchRef --jq .defaultBranchRef.name)"
echo "Installing into $REPO (default branch: $BRANCH)"

# -- 1. workflow files ------------------------------------------------------

# Files go in over git, not the contents API. The API refuses to write anything
# under .github/workflows/ unless the token carries the `workflow` OAuth scope,
# which `gh auth login` does not grant by default; a git push over SSH is not
# subject to that restriction. Clone once, write everything, push once.

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
git clone --quiet --depth 1 "git@github.com:$REPO.git" "$WORK/repo" 2>/dev/null \
  || git clone --quiet --depth 1 "https://github.com/$REPO.git" "$WORK/repo"

put_file() {
  local path="$1" content="$2"
  mkdir -p "$WORK/repo/$(dirname "$path")"
  local verb=created
  [ -f "$WORK/repo/$path" ] && verb=updated
  printf '%s\n' "$content" > "$WORK/repo/$path"
  echo "  $verb $path"
}

syndicate_yml="$(sed \
  -e '1c\
# Installed from '"$OWNER"'/syndicate (templates/syndicate.yml).\
# Push = dev.to DRAFT only. LinkedIn posts only via the manual publish run.' \
  -e "s|OWNER/syndicate|$OWNER/syndicate|" \
  -e "s|branches: \[main\]|branches: [$BRANCH]|" \
  "$HERE/templates/syndicate.yml")"
put_file ".github/workflows/syndicate.yml" "$syndicate_yml"

if [ "$WANT_TOKEN_CHECK" = "--token-check" ]; then
  token_yml="$(sed \
    -e '1,2c\
# Installed from '"$OWNER"'/syndicate (templates/linkedin-token-check.yml).\
# Weekly check; opens an issue when the LinkedIn token nears its 60-day expiry.' \
    -e "s|OWNER/syndicate|$OWNER/syndicate|" \
    "$HERE/templates/linkedin-token-check.yml")"
  put_file ".github/workflows/linkedin-token-check.yml" "$token_yml"
fi

# -- 2. CLAUDE.md guidance --------------------------------------------------
#
# An agent working in the *consuming* repo cannot see this repo's docs, so the
# rules that matter there -- opt-in frontmatter, the manual publish gate, the
# absolute-link requirement -- have to travel with the workflow.

claude_md="$(sed -n '/^```markdown$/,/^```$/p' "$HERE/docs/CLAUDE.md.snippet.md" \
             | sed '1d;$d' | sed "s|OWNER/REPO|$REPO|g")"

if [ -z "$claude_md" ]; then
  echo "  WARNING: could not extract the CLAUDE.md snippet; skipping." >&2
else
  if [ -f "$WORK/repo/CLAUDE.md" ] && grep -q '^## Syndication' "$WORK/repo/CLAUDE.md"; then
    echo "  CLAUDE.md already documents syndication; left alone"
  elif [ -f "$WORK/repo/CLAUDE.md" ]; then
    put_file "CLAUDE.md" "$(cat "$WORK/repo/CLAUDE.md")

$claude_md"
  else
    put_file "CLAUDE.md" "$claude_md"
  fi
fi

# -- commit and push whatever changed ---------------------------------------

if [ -n "$(git -C "$WORK/repo" status --porcelain)" ]; then
  git -C "$WORK/repo" add -A
  git -C "$WORK/repo" commit --quiet -m 'chore: install syndicate

Push creates a dev.to draft only; LinkedIn posting stays behind the manual
publish run. Nothing syndicates until a document opts in with a crosspost:
frontmatter block.'
  git -C "$WORK/repo" push --quiet origin "HEAD:$BRANCH"
  echo "  pushed $(git -C "$WORK/repo" rev-parse --short HEAD)"
else
  echo "  files already up to date; nothing to push"
fi

# -- 3. secrets and the issued-at variable ----------------------------------

set -a; . "$ENV_FILE"; set +a

for name in DEVTO_API_KEY LINKEDIN_ACCESS_TOKEN LINKEDIN_PERSON_URN; do
  value="${!name:-}"
  if [ -z "$value" ]; then
    echo "  skipped $name (empty in .env)"
    continue
  fi
  # No --body: gh reads the value from stdin when the flag is absent. Passing
  # `--body -` stores a literal "-" -- it looks like a stdin sentinel and is
  # not one, and the damage is invisible because GitHub then masks every "-"
  # in your logs as ***.
  printf '%s' "$value" | gh secret set "$name" --repo "$REPO"
  echo "  secret $name (${#value} chars)"
done

if [ -n "${LINKEDIN_TOKEN_ISSUED_AT:-}" ]; then
  gh variable set LINKEDIN_TOKEN_ISSUED_AT --repo "$REPO" --body "$LINKEDIN_TOKEN_ISSUED_AT"
  echo "  variable LINKEDIN_TOKEN_ISSUED_AT=$LINKEDIN_TOKEN_ISSUED_AT"
else
  echo "  WARNING: LINKEDIN_TOKEN_ISSUED_AT unset; the expiry warning cannot count down." >&2
fi

echo
echo "Done. Nothing syndicates until a document carries a crosspost: block:"
echo
echo "  ---"
echo "  title: Your title"
echo "  crosspost:"
echo "    devto: full"
echo "    linkedin: summary"
echo "    summary: |"
echo "      The short version that goes on LinkedIn."
echo "  ---"
