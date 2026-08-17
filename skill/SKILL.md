---
name: st-announce
description: Stranger Things release-cycle announcements for the "Test Channel - More coming up!" Teams channel. Preview, regenerate, or manually post the Monday-Thursday release-cut announcement cards, and check the GitHub Actions schedule that posts them automatically at 7am Pacific. Use when asked to preview an announcement, rebuild the banner artwork, post an announcement early or off-schedule, or diagnose why an announcement did or did not appear in Teams.
---

# Stranger Things Release-Cycle Announcements

Posts a themed announcement card to the **Test Channel - More coming up!** channel of
the **Stranger Things** team, Monday through Thursday at 07:00 America/Los_Angeles.

Scheduling is owned by GitHub Actions (`.github/workflows/announce.yml`), not by this
skill and not by the local machine. This skill is for previewing, regenerating artwork,
and posting off-schedule.

## Schedule and content

| Day | Title | Description |
|---|---|---|
| Monday | Be ready for the Release Cut... | Prepare your work, and have all possible reviews before going to Staging on Wednesday. |
| Tuesday | The Release Cut is tomorrow... | It's time to check if your work is really ready for Staging. |
| Wednesday | Release Cut Day! | The release cut will be made around 8pm PST. |
| Thursday | DEPLOYMENT DAY! | The production deployment will be made around 9pm PST. |

Each card carries a 1200x628 banner plus **every** currently open pull request in
`nrgmr/research-platform-ui`, marked 🚧 draft or 👀 awaiting review, drafts first.

**A day's card only posts on that day.** `FORCE_DAY` previews any day freely, but posting
one out of step is refused unless `ALLOW_OFF_DAY=1` is set deliberately.

Cards also carry `📅 Release Cut: MM/DD | Deployment: MM/DD` and a `🏷️ OIQ / LIQ` label.
The cut runs every 14 days from `release_cycle.anchor`, not weekly, so on an off-week the
line reads "Next Release Cut" and points at the next real one. If the upstream anchor in
`auto-release-branch.yaml` moves, update `release_cycle.anchor` — drift is not detected.

Copy lives in `announcements.json`. Edit there, never in the scripts. Each day also
carries an `emoji`, a bold `hype` lead, and a `checklist` of emoji reminders; `mention`
and `footer` are top-level.

`mention` renders as `📢 @everyone` but **notifies nobody** — Adaptive Cards support only
specific-user mentions via AAD IDs, and channel-wide mentions are not available over an
incoming webhook. Do not let anyone assume it pings the team.

## Commands

### preview
Print the Adaptive Card JSON without posting. Always do this before `post`.

```bash
DRY_RUN=1 FORCE_DAY=Wednesday \
GITHUB_REPOSITORY=<owner>/<repo> ASSET_REF=main \
GH_PAT="$(gh auth token)" \
python3 scripts/post_announcement.py
```

### post
Send a specific day's card to Teams immediately. Requires `TEAMS_WEBHOOK_URL`.

**Show the user the previewed card and get explicit confirmation before running this.**
It posts to a shared channel and cannot be unsent.

```bash
TEAMS_WEBHOOK_URL="$(...)" FORCE_DAY=Wednesday \
GITHUB_REPOSITORY=<owner>/<repo> ASSET_REF=main \
GH_PAT="$(gh auth token)" \
python3 scripts/post_announcement.py
```

Preferred alternative, which keeps the secret in GitHub rather than the local shell:

```bash
gh workflow run announce.yml -f force_day=Wednesday -f dry_run=false
```

### art
Rebuild the four banners after changing copy, imagery, or styling. Requires Pillow and
the Messina Sans OTFs installed locally; it downloads the source stills itself.

The source-image cache is keyed on the URL, so swapping an image in
`announcements.json` re-downloads rather than reusing a stale file.

```bash
python3 scripts/make_art.py
```

Then commit the regenerated `assets/*.png`.

### status
Check recent scheduled runs and whether the secrets are present.

```bash
gh run list --workflow=announce.yml --limit 10
gh secret list
```

## Operational notes

- **The weekday gate is in the script, not the cron.** The workflow fires at both 14:00
  and 15:00 UTC so that one of them is always 07:00 Pacific across DST. The script exits
  quietly on the run whose Pacific hour is not 7. Seeing one skipped run per day in the
  Actions log is correct behaviour, not a fault.
- **A bare `{"text": ...}` payload will not work.** Power Automate returns HTTP 202 and
  then fails silently inside the flow run. The full Adaptive Card attachment envelope is
  mandatory.
- **Card images must be publicly reachable.** Teams fetches them server-side, so the
  assets repository has to stay public and the raw URLs have to resolve unauthenticated.
- **A GitHub API failure must never block the announcement.** `fetch_open_prs` returns an
  error string instead of raising, and the card posts without the PR section. Preserve
  that behaviour when editing.
- **`GH_PAT` is required only because the PR repo is private.** The workflow's built-in
  `GITHUB_TOKEN` cannot read `nrgmr/research-platform-ui` from a different repository.
