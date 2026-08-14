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
15:00 UTC, and `post_announcement.py` checks the real Pacific hour and exits quietly
unless it is 07:00.

One skipped run per day in the Actions log is expected and correct.

## Manual runs

```bash
# Preview without posting
gh workflow run announce.yml -f force_day=Wednesday -f dry_run=true

# Post immediately
gh workflow run announce.yml -f force_day=Wednesday -f dry_run=false
```

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

Requires Python 3 with Pillow. Anton SC is vendored, so only the optional Messina Sans
body face depends on local installation:

```bash
python3 -m pip install --upgrade Pillow
python3 scripts/make_art.py
git add assets && git commit -m "chore: regenerate announcement banners"
```

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
