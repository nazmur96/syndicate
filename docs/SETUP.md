# Setup

One-time credential setup, then the two files a consuming repo needs, then the
runbook for the LinkedIn re-auth you will have to do roughly six times a year.

Budget about twenty minutes for the first repo. Every repo after that is two
files and no credentials, because the secrets live at the account or
organisation level, not in each repo.

---

## 1. dev.to

1. Go to <https://dev.to/settings/extensions> → **DEV Community API Keys**.
2. Give the key a name that says where it is used (`syndicate`), and generate it.
3. Copy it now. dev.to shows it once.

The key is not scoped and does not expire. It can publish, edit, and delete
articles on your account, so treat it as a full-account credential: account-level
GitHub secret, never a repo file, never a log line.

Rate limit: **10 requests per 30 seconds**. The adapter honours `Retry-After`,
and one document costs at most three requests, so this only becomes real if you
push thirty documents at once.

## 2. LinkedIn

Longer, because LinkedIn has no API-key equivalent — you are doing an OAuth
flow by hand and pasting the result into a secret.

### 2a. Create the app

1. <https://www.linkedin.com/developers/apps> → **Create app**. It must be
   associated with a LinkedIn *Page*; make an empty one if you have none.
2. **Products** tab → request **Share on LinkedIn** and **Sign In with LinkedIn
   using OpenID Connect**. Both are self-serve and usually granted immediately.
   - `Share on LinkedIn` gives `w_member_social` — the scope that posts.
   - `Sign In with LinkedIn` gives `openid` and `profile` — not needed to post,
     but they are what lets `syndicate check` confirm the token actually belongs
     to the person in `LINKEDIN_PERSON_URN`. Without them that check is
     inconclusive rather than failed, which is a worse place to be.
3. **Auth** tab → note the **Client ID** and **Client Secret**, and add an
   authorised redirect URL. `http://localhost:8000/callback` is fine; nothing
   has to be listening, you are going to read the code out of the address bar.

### 2b. Get a token

Open this in a browser, signed in as the account that should own the posts:

```
https://www.linkedin.com/oauth/v2/authorization
  ?response_type=code
  &client_id=YOUR_CLIENT_ID
  &redirect_uri=http://localhost:8000/callback
  &state=anything
  &scope=w_member_social%20openid%20profile
```

(all on one line). Approve, and the browser lands on a dead `localhost` page
whose URL contains `?code=...`. Copy that code — it is valid for about 30
seconds, so have the next command ready.

```bash
curl -s -X POST https://www.linkedin.com/oauth/v2/accessToken \
  -d grant_type=authorization_code \
  -d code=THE_CODE \
  -d redirect_uri=http://localhost:8000/callback \
  -d client_id=YOUR_CLIENT_ID \
  -d client_secret=YOUR_CLIENT_SECRET
```

The response carries `access_token` and `expires_in`. That `expires_in` is
`5184000` — sixty days. **Write down today's date**; you need it in step 3.

### 2c. Get your person URN

```bash
curl -s https://api.linkedin.com/v2/userinfo -H "Authorization: Bearer THE_TOKEN"
```

Take the `sub` field. Your URN is `urn:li:person:<sub>`.

This adapter posts to a personal profile only. Posting as an organisation needs
`w_organization_social` and a different author URN, which is not implemented.

## 3. Store the credentials

Set these once, at the account or organisation level, so every repo inherits
them (GitHub: *Settings → Secrets and variables → Actions*).

**Secrets:**

| Name | Value |
|---|---|
| `DEVTO_API_KEY` | from step 1 |
| `LINKEDIN_ACCESS_TOKEN` | from step 2b |
| `LINKEDIN_PERSON_URN` | `urn:li:person:...` from step 2c |

**Variable** (not a secret — it is a date, and the warning workflow needs to
read it):

| Name | Value |
|---|---|
| `LINKEDIN_TOKEN_ISSUED_AT` | the date from step 2b, `YYYY-MM-DD` |

Skip the LinkedIn three entirely if you only want dev.to. Missing credentials
are a logged skip, not a failure.

### Locally

Copy `.env.example` to `.env` (gitignored) and fill in the same names. Real
environment variables win over `.env`, which is how CI injection works.

```bash
syndicate check          # read-only: proves the credentials work
syndicate token-status   # how long the LinkedIn token has left
```

## 4. Add the workflows to a repo

Copy `templates/syndicate.yml` to `.github/workflows/syndicate.yml` and replace
`OWNER` with the account hosting this tool. That is the whole install.

If the repo posts to LinkedIn, also copy `templates/linkedin-token-check.yml` to
`.github/workflows/linkedin-token-check.yml`. It is the weekly expiry warning,
and it has to live in your repo because a `schedule:` trigger only fires in the
repository that declares it.

Then paste the snippet in [`docs/CLAUDE.md.snippet.md`](CLAUDE.md.snippet.md)
into the repo's `CLAUDE.md`, so an agent working in that repo knows the
frontmatter contract and the human gate.

### Canonical URLs

Canonical URLs are derived from `$GITHUB_REPOSITORY` and point at the repo's
GitHub Pages site — `owner/notes` becomes `https://owner.github.io/notes/...`.
On a custom domain, pass it once in the caller:

```yaml
    with:
      pages-base-url: https://notes.example.com
```

A single document can override its own with `crosspost.canonical_url`.

## 5. First run

1. Add a `crosspost:` block to a document (see the README, or
   `examples/example-post.md`).
2. Push to `main`. The workflow creates a **dev.to draft** and stops.
3. Read the draft at <https://dev.to/dashboard>. Nothing is public yet, and
   LinkedIn has not been contacted.
4. When it reads right: **Actions → Syndicate → Run workflow**, mode
   `publish`, and put the document's path in `paths`.

Step 4 is the human gate, and it is deliberately a different gesture from
pushing. Publish mode refuses to run without explicit paths.

---

## LinkedIn re-auth runbook

**When:** every 60 days, or whenever the weekly check opens the
*"LinkedIn access token needs re-authorising"* issue.

**Why it is manual:** LinkedIn member tokens cannot be refreshed
programmatically without approved partner access. There is no automation to
write here — only a shorter path through the thing you have to do by hand.

**What breaks while it is expired:** LinkedIn posting only. dev.to drafting and
publishing carry on; the orchestrator fails soft per platform. Nothing is queued
or retried — a document whose LinkedIn post failed stays unposted until you
publish it again.

**Steps** (about three minutes):

1. Redo **step 2b** above. Same app, same authorisation URL — you do *not* need
   a new app, new products, or a new client secret.
2. Update the `LINKEDIN_ACCESS_TOKEN` secret with the new token.
3. Update the `LINKEDIN_TOKEN_ISSUED_AT` variable to today's date. **This is the
   step people skip**, and skipping it means the warning is computed from a
   stale date and fires at the wrong time — or, worse, the status goes `unknown`
   and stops telling you anything.
4. Verify:
   ```bash
   syndicate token-status
   ```
   or run the **LinkedIn token check** workflow manually. A healthy result closes
   the issue on its own.

**If the issue says `unknown`:** the token answers but `LINKEDIN_TOKEN_ISSUED_AT`
is not set. Set it to the date you last did step 2b. If you cannot remember,
re-auth now and set it to today — that costs three minutes and buys back a
reliable warning.

**If the issue says `expired` but the date looks fine:** the token was revoked
early — someone removed the app, or changed the account password. Re-auth.
