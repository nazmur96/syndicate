#!/usr/bin/env bash
#
# Install the syndicate caller workflow into one of your repositories.
#
#   scripts/install-into.sh OWNER/REPO [--token-check]
#
# Does three things the manual route makes you do by hand:
#   1. writes .github/workflows/syndicate.yml, with the owner and the repo's
#      actual default branch filled in (not every repo is on `main`);
#   2. copies DEVTO_API_KEY / LINKEDIN_ACCESS_TOKEN / LINKEDIN_PERSON_URN from
#      your local .env into that repo's Actions secrets, and
#      LINKEDIN_TOKEN_ISSUED_AT into its Actions variables;
#   3. with --token-check, also installs the weekly expiry reminder. Install
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

put_file() {
  local path="$1" content="$2" message="$3" sha args
  sha="$(gh api "repos/$REPO/contents/$path" --jq .sha 2>/dev/null || true)"
  args=(-X PUT "repos/$REPO/contents/$path"
        -f message="$message"
        -f branch="$BRANCH"
        -f content="$(printf '%s' "$content" | base64 -w0)")
  [ -n "$sha" ] && args+=(-f sha="$sha")
  gh api "${args[@]}" --jq '.commit.sha' >/dev/null
  echo "  ${sha:+updated }${sha:-created }$path"
}

syndicate_yml="$(sed \
  -e '1c\
# Installed from '"$OWNER"'/syndicate (templates/syndicate.yml).\
# Push = dev.to DRAFT only. LinkedIn posts only via the manual publish run.' \
  -e "s|OWNER/syndicate|$OWNER/syndicate|" \
  -e "s|branches: \[main\]|branches: [$BRANCH]|" \
  "$HERE/templates/syndicate.yml")"
put_file ".github/workflows/syndicate.yml" "$syndicate_yml" "chore: install syndicate workflow"

if [ "$WANT_TOKEN_CHECK" = "--token-check" ]; then
  token_yml="$(sed \
    -e '1,2c\
# Installed from '"$OWNER"'/syndicate (templates/linkedin-token-check.yml).\
# Weekly check; opens an issue when the LinkedIn token nears its 60-day expiry.' \
    -e "s|OWNER/syndicate|$OWNER/syndicate|" \
    "$HERE/templates/linkedin-token-check.yml")"
  put_file ".github/workflows/linkedin-token-check.yml" "$token_yml" "chore: install LinkedIn token check"
fi

# -- 2. secrets and the issued-at variable ----------------------------------

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
