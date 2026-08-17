# Stranger Things Release-Cycle Announcements

Posts a themed announcement card to the **Test Channel - More coming up!** channel of the
**Stranger Things** Microsoft Teams team, Monday through Thursday at **07:00
America/Los_Angeles**.

Each card carries a 1200x628 banner and the current open pull requests in
`nrgmr/research-platform-ui`, labelled **Draft** or **Open**.

| Day | Announcement |
|---|---|
| Monday | Be ready for the Release Cut... |
| Tuesday | The Release Cut is tomorrow... |
| Wednesday | Release Cut Day! |
| Thursday | DEPLOYMENT DAY! |

## Why this repository is public

Microsoft Teams renders Adaptive Card images **server-side**, fetching each image URL
without the viewer's credentials. Private `raw.githubusercontent.com` URLs are
short-lived and signed, so they do not render. The banners therefore have to be served
from a public repository for the cards to display at all.

## Layout

```
announcements.json              Copy, imagery, and target repo. Edit copy here only.
assets/*.png                    The four 1200x628 banners, served raw to Teams.
scripts/post_announcement.py    Builds and posts the Adaptive Card.
scripts/make_art.py             Regenerates the banners from the source stills.
skill/SKILL.md                  Claude Code skill for previewing and posting manually.
brand/nrg-logo.{svg,png}        NRG mark, from design-system packages/ui/src/nrg-logo.tsx.
brand/fonts/AntonSC-Regular.ttf Title face, vendored under the OFL (see brand/fonts/OFL.txt).
.github/workflows/announce.yml  The 7am Pacific schedule.
```

## Setup

Two repository secrets are required (**Settings → Secrets and variables → Actions**):

| Secret | Purpose |
|---|---|
| `TEAMS_WEBHOOK_URL` | Power Automate "when a webhook request is received" trigger URL for the target channel. |
| `GH_PAT` | Token with read access to the private `nrgmr/research-platform-ui`, so the card can list open PRs. |

To create the webhook: in Teams, open the channel's **…** menu → **Workflows** → template
**"Post to a channel when a webhook request is received"** → select the team and channel →
**Add workflow**. The URL is shown once. Treat it as a credential; anyone holding it can
post to the channel.

Without `GH_PAT` the card still posts, minus the pull request section.

## Scheduling and daylight saving

GitHub cron is UTC-only and does not follow US daylight saving, so a single cron entry
would drift by an hour twice a year. The workflow instead fires at **both** 14:00 and
15:00 UTC, and `post_announcement.py` keeps whichever cron corresponds to 07:00 Pacific
today.

It decides from `github.event.schedule` — the cron that actually triggered the run — not
from the wall clock. GitHub's scheduler is best-effort, and a run delayed past the hour
boundary would otherwise skip the announcement for that day entirely, silently.

One skipped run per day in the Actions log is expected and correct.

Manual dispatches carry no `SCHEDULE_CRON`, so they post today's card at any hour.

### ⚠️ GitHub disables idle scheduled workflows

GitHub turns off scheduled workflows after **60 days with no repository activity**. This
repo is otherwise idle — the banners rarely change — so the Monday–Thursday post will
eventually stop on its own. GitHub emails the repo owner first, and any push (or re-enabling
the workflow from the Actions tab) resets the clock.

## Manual runs

```bash
# Preview without posting -- any day, any time
gh workflow run announce.yml -f force_day=Wednesday -f dry_run=true

# Post today's card immediately
gh workflow run announce.yml -f dry_run=false
```

**A day's card only posts on that day.** Asking to post Wednesday's card on a Friday is
refused, because it would misinform the team about the release schedule. Previewing is
always allowed. To override deliberately, add `-f allow_off_day=true`.

Locally:

```bash
DRY_RUN=1 FORCE_DAY=Monday \
GITHUB_REPOSITORY=<owner>/<repo> ASSET_REF=main \
GH_PAT="$(gh auth token)" \
python3 scripts/post_announcement.py
```

## Regenerating the artwork

`scripts/make_art.py` downloads the source stills and composites the banners using the
NRG NEXT Design System palette (`#4c003e` purple, `#89119f` bright purple, `#ff4d42` red,
`#ffe21a` yellow). Titles are set in **Anton SC** (all caps), vendored under `brand/fonts/`
with its OFL licence so the art regenerates identically anywhere. Body copy uses Messina
Sans when installed locally, falling back to Helvetica Neue. The NRG mark in `brand/` is
rasterised from the design system's `nrg-logo.tsx`.

Requires Python 3 with Pillow. Anton SC is vendored, so the titles render identically
anywhere. The body face falls back Messina Sans → Helvetica Neue (macOS) → DejaVu Sans
(Linux) → Pillow's built-in default, so the script runs on a fresh checkout on any platform,
though only a machine with Messina Sans installed reproduces the committed banners exactly:

```bash
python3 -m pip install --upgrade Pillow
python3 scripts/make_art.py
git add assets && git commit -m "chore: regenerate announcement banners"
```

## Release dates and products

Every card carries a dates line and a product label:

```
📅 Release Cut: 08/19  |  Deployment: 08/20
🏷️ OIQ / LIQ
```

The cut is **not weekly**. `nrgmr/research-platform-ui` cuts every 14 days from a fixed
anchor (`auto-release-branch.yaml`), so roughly half of Wednesdays have no cut at all.
`release_cycle` in `announcements.json` mirrors that anchor and interval.

On release cut day the date is marked `(today)`, and on deployment day the deployment date
is marked the same way.

**The dates line is always rendered.** If `release_cycle` is missing or malformed, it falls
back to this week's Wednesday and Thursday and logs a warning, rather than dropping the line
from the card.

On a week with no cut, the label reads **"Next Release Cut"** and points at the next real
one, rather than inventing a date for the current week. Note the day headlines are still
fixed copy, so Wednesday of an off-week reads "Release Cut Day!" above a "Next Release Cut"
date — reword the Wednesday title if that bothers you.

If the anchor ever moves, change `release_cycle.anchor` here to match the upstream workflow;
nothing detects drift automatically.

## Card copy

Each day's card is assembled from `announcements.json`:

| Key | Purpose |
|---|---|
| `emoji` | Prefixes the headline. |
| `hype` | Short bold lead sentence before the description. |
| `checklist` | Emoji bullet reminders. Keep these actionable and do not restate the description. |
| `mention` | Top-level. Rendered as `📢 @everyone`. |
| `footer` | Top-level. Subtle line at the bottom of every card. |

**`mention` does not notify anyone.** Adaptive Cards can mention specific users through
`msteams.entities` with AAD IDs, but there is no channel-wide mention available over an
incoming webhook. The line is presentational only.

Open pull requests are marked 🚧 for draft and 👀 for awaiting review, drafts first. **Every
open PR is listed.** The list is trimmed only if the card would exceed `MAX_PAYLOAD_BYTES`
(24KB), because Teams rejects oversized cards outright and the whole announcement would be
lost rather than a few entries.

## Per-day framing

Each entry in `announcements.json` accepts three optional keys for shots that need
different handling:

| Key | Default | Effect |
|---|---|---|
| `zoom` | `1.0` | Push in on the subject. `1.5` crops to two-thirds of the frame. |
| `focus` | `[0.5, 0.5]` | Normalised point to keep centred. Clamped so the crop stays in bounds. |
| `punch` | `0.0` | Extra contrast and saturation for flat frames. |
| `saturation` | `0.72` | Global desaturation. Raise to `1.0` for frames that are already strongly graded. |
| `wash` | `48` | Alpha of the brand-purple wash. Set to `0` where it would muddy the source colour. |

Thursday sets `saturation: 1.0` and `wash: 0` because it uses the official season 5 key
art, which is already a deliberate deep red. The default grade would have desaturated it
and pushed purple over the top, dulling exactly what makes that frame work.

## Image credits

Banner backgrounds are promotional stills from *Stranger Things* (Netflix), used for
internal team communication. Source URLs are recorded per day in `announcements.json`.
