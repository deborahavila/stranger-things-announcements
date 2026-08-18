#!/usr/bin/env python3
"""Post the daily release-cycle announcement to Microsoft Teams.

Runs Monday-Thursday at 07:00 America/Los_Angeles. GitHub cron is UTC-only and
does not follow US daylight saving, so the workflow fires at both 14:00 and
15:00 UTC and this script keeps whichever cron corresponds to 07:00 Pacific
today. It decides from the triggering cron rather than the wall clock, because
GitHub's scheduler is best-effort and a delayed run would otherwise be skipped.

Environment:
  TEAMS_WEBHOOK_URL  (required)  Power Automate "when a webhook request is
                                 received" trigger URL for the target channel.
  GH_PAT             (optional)  Token with read access to the private PR repo.
                                 Without it the card still posts, minus the PR
                                 section.
  GITHUB_REPOSITORY  (optional)  owner/repo, used to build raw asset URLs.
  ASSET_REF          (optional)  Branch or commit SHA for asset URLs.
  SCHEDULE_CRON      (optional)  The cron expression that triggered a scheduled
                                 run (github.event.schedule). Used to pick the
                                 correct DST run without trusting the clock.
  FORCE_DAY          (optional)  Monday..Thursday. Previewing another day is
                                 always allowed; POSTING one out of step needs
                                 ALLOW_OFF_DAY=1.
  ALLOW_OFF_DAY      (optional)  "1" permits posting a day's card on another day.
  DRY_RUN            (optional)  "1" prints the payload instead of posting.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
POST_HOUR = 7
# Weekday names follow LC_TIME when taken from strftime, but the config keys
# are English, so a set locale would silently match nothing and never post.
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((REPO_ROOT / "announcements.json").read_text(encoding="utf-8"))

# Every open PR is listed. Teams rejects cards beyond roughly 28KB outright, so
# the list is trimmed only as a last resort, to stop an unusually large day from
# failing the post entirely. See trim_to_fit().
MAX_PAYLOAD_BYTES = 24_000
# Safety stop for pagination; 10 pages is 1000 open PRs.
MAX_PR_PAGES = 10
# Production deploys the SECOND Thursday after the Wednesday cut, not the next
# day. Verified against 14 consecutive prd-pipeline builds on Jenkins.
DEFAULT_DEPLOY_OFFSET_DAYS = 8
# Used only when release_cycle.cut_weekday is absent or unrecognised.
DEFAULT_CUT_WEEKDAY = "Wednesday"
HTTP_TIMEOUT = 30


def log(msg: str) -> None:
    print(msg, flush=True)


def resolve_day() -> str | None:
    """Return the weekday name to post for, or None if we should stay quiet."""
    now = datetime.now(PACIFIC)
    today = WEEKDAYS[now.weekday()]

    forced = os.environ.get("FORCE_DAY", "").strip()
    if forced:
        if forced not in CONFIG["days"]:
            sys.exit(f"FORCE_DAY={forced!r} is not one of {list(CONFIG['days'])}")

        # Each day's card belongs to that day. Previewing another day is fine,
        # but posting one to the channel out of step -- a Wednesday card on a
        # Friday -- misinforms the team about the release schedule.
        off_day = forced != today
        previewing = os.environ.get("DRY_RUN") == "1"
        allowed = os.environ.get("ALLOW_OFF_DAY") == "1"
        if off_day and not previewing and not allowed:
            sys.exit(
                f"Refusing to post the {forced} card on a {today}.\n"
                f"Each day's announcement is only posted on that day.\n"
                f"Use DRY_RUN=1 to preview it, or ALLOW_OFF_DAY=1 to override deliberately."
            )
        if off_day:
            log(f"FORCE_DAY={forced} on a {today} ({'preview' if previewing else 'override'}).")
        else:
            log(f"FORCE_DAY set; posting the {forced} announcement.")
        return forced

    day = today
    log(f"Pacific time is {now:%Y-%m-%d %H:%M %Z} ({day}).")

    if day not in CONFIG["days"]:
        log(f"{day} has no announcement configured. Nothing to do.")
        return None

    schedule = os.environ.get("SCHEDULE_CRON", "").strip()
    if not schedule:
        # Manual run. Post today's card whatever the hour -- gating a manual
        # dispatch on the clock would make it silently do nothing.
        log("Manual run; posting today's announcement.")
        return day

    # Scheduled run. Two crons exist so that one of them is 07:00 Pacific in
    # either DST state. Decide from the cron that actually triggered this run,
    # NOT from the wall clock: GitHub's scheduler is best-effort, and a run
    # delayed past the hour boundary would otherwise skip the day entirely.
    offset_hours = int(now.utcoffset().total_seconds() // 3600)
    intended_utc_hour = (POST_HOUR - offset_hours) % 24
    try:
        cron_utc_hour = int(schedule.split()[1])
    except (IndexError, ValueError):
        log(f"Could not parse SCHEDULE_CRON={schedule!r}; falling back to the hour check.")
        if now.hour != POST_HOUR:
            log(f"Pacific hour is {now.hour}, not {POST_HOUR}; skipping.")
            return None
        return day

    if cron_utc_hour != intended_utc_hour:
        log(f"Cron {schedule!r} is the {'PST' if intended_utc_hour == 14 else 'PDT'} "
            f"duplicate today (07:00 Pacific is {intended_utc_hour:02d}:00 UTC); skipping.")
        return None

    log(f"Cron {schedule!r} is today's 07:00 Pacific run.")
    return day


def release_dates(today: date) -> tuple[date, date, str]:
    """(cut, deployment, week_kind) for the release this card should reference.

    week_kind is "cut_week" or "deploy_week" and selects which copy variant the
    card uses, so a headline never announces an event that is not happening.

    The cut is NOT weekly: research-platform-ui cuts every 14 days from a fixed
    anchor, so roughly half of cut weekdays have none. Announcements still run
    every weekday, so on an off-week the card describes the release already in
    flight -- the PRIOR cut and its upcoming deployment -- rather than the next
    future cut, which is still two weeks out.
    """
    cyc = CONFIG.get("release_cycle") or {}

    # Which weekday the cut lands on, so moving the upstream cut is a config
    # change rather than a code change.
    name = cyc.get("cut_weekday", DEFAULT_CUT_WEEKDAY)
    try:
        cut_dow = WEEKDAYS.index(name)
    except ValueError:
        log(f"cut_weekday={name!r} is not a weekday name; assuming {DEFAULT_CUT_WEEKDAY}.")
        cut_dow = WEEKDAYS.index(DEFAULT_CUT_WEEKDAY)

    # The dates line is always rendered. If the cycle config is missing or
    # broken, fall back to this week's cut weekday rather than dropping the
    # line, so a config mistake never silently removes it from the card.
    cut_day = today + timedelta(days=cut_dow - today.weekday())
    fallback = (cut_day, cut_day + timedelta(days=DEFAULT_DEPLOY_OFFSET_DAYS), "cut_week")

    if not cyc:
        return fallback
    try:
        anchor = date.fromisoformat(cyc["anchor"])
        interval = int(cyc["interval_days"])
        if interval < 1:
            raise ValueError("interval_days must be positive")
    except (KeyError, ValueError) as exc:
        log(f"release_cycle is misconfigured ({exc}); using this week's {name}.")
        return fallback

    def is_cut(d: date) -> bool:
        delta = (d - anchor).days
        return delta >= 0 and delta % interval == 0

    offset = int(cyc.get("deploy_offset_days", DEFAULT_DEPLOY_OFFSET_DAYS))

    # Cut weeks and deploy weeks alternate, because deployment lands cut+8d --
    # the Thursday of the following week. On a cut week the card is about the
    # cut happening now; on a deploy week it is about the release already in
    # flight, NOT the next future cut, which is still two weeks out.
    if is_cut(cut_day):
        return cut_day, cut_day + timedelta(days=offset), "cut_week"

    prior = cut_day
    for _ in range(8):
        prior -= timedelta(days=7)
        if is_cut(prior):
            return prior, prior + timedelta(days=offset), "deploy_week"

    log(f"No recent release cut found; using this week's {name}.")
    return fallback


def gh_api(path: str, token: str) -> list[dict]:
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "stranger-things-announcer",
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_open_prs() -> tuple[list[dict], str | None]:
    """Open PRs (draft and ready) for the configured repo.

    Returns (prs, error). A failure here must not block the announcement, so
    the error is returned rather than raised.
    """
    token = os.environ.get("GH_PAT", "").strip()
    if not token:
        return [], "GH_PAT is not set"

    repo = CONFIG["pr_repo"]
    raw: list[dict] = []
    try:
        # Paginate: a single per_page=100 request would silently drop anything
        # beyond the first hundred, and the card is meant to list every open PR.
        for page in range(1, MAX_PR_PAGES + 1):
            batch = gh_api(
                f"/repos/{repo}/pulls?state=open&sort=updated&direction=desc"
                f"&per_page=100&page={page}",
                token,
            )
            if not isinstance(batch, list):
                return [], "unexpected GitHub response (not a list of pull requests)"
            raw.extend(batch)
            if len(batch) < 100:
                break
        else:
            log(f"Reached the {MAX_PR_PAGES}-page cap at {len(raw)} PRs; there may be more.")

        # Shaping stays inside the try. An unexpected payload shape must degrade
        # to "no PR section" like any other API failure, never kill the post.
        prs = [
            {
                "number": pr["number"],
                "title": pr["title"],
                "url": pr["html_url"],
                "author": (pr.get("user") or {}).get("login", "unknown"),
                "status": "Draft" if pr.get("draft") else "Open",
            }
            for pr in raw
        ]
    except urllib.error.HTTPError as exc:
        return [], f"GitHub API returned HTTP {exc.code} for {repo}"
    except Exception as exc:  # noqa: BLE001 - degrade gracefully, never block the post
        return [], f"GitHub API call failed: {type(exc).__name__}: {exc}"

    # Drafts first: those are the ones at risk for the cut.
    prs.sort(key=lambda p: (p["status"] != "Draft", -p["number"]))
    return prs, None


def asset_url(asset: str) -> str:
    owner_repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    ref = os.environ.get("ASSET_REF", "").strip() or "main"
    if not owner_repo:
        # Local preview only. Teams fetches images server-side and cannot read a
        # file:// URL, so refuse rather than post a card with a broken image.
        if os.environ.get("DRY_RUN") != "1":
            sys.exit("GITHUB_REPOSITORY is not set, so the card image would be a local "
                     "file:// URL that Teams cannot fetch. Set it, or use DRY_RUN=1.")
        return str((REPO_ROOT / "assets" / asset).as_uri())
    return CONFIG["asset_base"].format(owner_repo=owner_repo, ref=ref) + f"/{asset}"


def build_card(day: str, prs: list[dict], pr_error: str | None) -> dict:
    day_cfg = CONFIG["days"][day]
    today = datetime.now(PACIFIC).date()
    cut, deploy, week_kind = release_dates(today)

    # Per-week-type copy. Fall back to the day entry itself so an older
    # single-variant config still renders.
    cfg = {**day_cfg, **day_cfg.get(week_kind, {})}
    body: list[dict] = [
        {
            "type": "Image",
            "url": asset_url(cfg["asset"]),
            "size": "Stretch",
            "altText": f"{day}: {cfg['title']}",
        },
    ]

    # Decoration only. Adaptive Cards can mention specific users via
    # msteams.entities, but there is no channel-wide mention over an incoming
    # webhook, so this renders as text and notifies nobody.
    mention = CONFIG.get("mention")
    if mention:
        body.append({
            "type": "TextBlock",
            "text": f"📢 **{mention}**",
            "wrap": True,
            "spacing": "Medium",
        })

    # The dates line is always present -- release_dates never returns None.
    label = "Release Cut"
    cut_txt = f"{cut:%m/%d}" + (" (today)" if cut == today else "")
    deploy_txt = f"{deploy:%m/%d}" + (" (today)" if deploy == today else "")
    body.append({
        "type": "TextBlock",
        "text": f"📅 **{label}:** {cut_txt}  |  **Deployment:** {deploy_txt}",
        "wrap": True,
        "spacing": "Small",
    })

    products = CONFIG.get("products")
    if products:
        body.append({
            "type": "TextBlock",
            "text": f"🏷️ **{products}**",
            "wrap": True,
            "spacing": "None",
        })

    emoji = cfg.get("emoji", "")
    headline = f"{emoji} {cfg['title'].upper()}".strip()
    body.append({
        "type": "TextBlock",
        "text": headline,
        "weight": "Bolder",
        "size": "Large",
        "wrap": True,
        "spacing": "Small",
    })

    hype = cfg.get("hype")
    lead = f"**{hype}** {cfg['description']}" if hype else cfg["description"]
    body.append({
        "type": "TextBlock",
        "text": lead,
        "wrap": True,
        "spacing": "Small",
    })

    for item in cfg.get("checklist", []):
        body.append({
            "type": "TextBlock",
            "text": item,
            "wrap": True,
            "spacing": "Small",
        })

    body.extend(_pr_section(prs, pr_error))

    footer = CONFIG.get("footer")
    if footer:
        body.append({
            "type": "TextBlock",
            "text": f"_{footer}_",
            "wrap": True,
            "isSubtle": True,
            "size": "Small",
            "spacing": "Medium",
            "separator": True,
        })

    return envelope(body, day, cfg)


# Draft PRs are the ones at risk for the cut, so they get the attention-seeking
# marker; everything else is simply awaiting eyes.
STATUS_EMOJI = {"Draft": "🚧", "Open": "👀"}

# Marks the block as a pull request entry. Do NOT identify these by their emoji
# prefix: checklist items legitimately use the same emoji (Wednesday has
# "👀 Watch for the release branch to appear"), and trimming would then be able
# to delete announcement copy instead of a pull request.
PR_LINK_MARKER = "/pull/"


def is_pr_line(block: dict) -> bool:
    return block.get("type") == "TextBlock" and PR_LINK_MARKER in block.get("text", "")


def _pr_section(prs: list[dict], pr_error: str | None) -> list[dict]:
    repo = CONFIG["pr_repo"]
    if pr_error:
        return [{
            "type": "TextBlock",
            "text": f"⚠️ _Open pull requests unavailable: {pr_error}._",
            "wrap": True,
            "isSubtle": True,
            "spacing": "Medium",
            "separator": True,
        }]

    drafts = sum(1 for p in prs if p["status"] == "Draft")
    ready = len(prs) - drafts
    blocks = [{
        "type": "TextBlock",
        "text": f"🔀 **Still open in {repo.split('/')[-1]}** — 👀 {ready} open · 🚧 {drafts} draft",
        "wrap": True,
        "spacing": "Medium",
        "separator": True,
    }]

    if not prs:
        blocks.append({
            "type": "TextBlock",
            "text": "🎉 Nothing open. The board is clear.",
            "wrap": True,
            "spacing": "Small",
        })
        return blocks

    for pr in prs:
        title = pr["title"]
        if len(title) > 90:
            title = title[:87].rstrip() + "…"
        marker = STATUS_EMOJI.get(pr["status"], "•")
        blocks.append({
            "type": "TextBlock",
            "text": f"{marker} [#{pr['number']}]({pr['url']}) {title} — _{pr['author']}_",
            "wrap": True,
            "spacing": "Small",
        })

    return blocks


def trim_to_fit(payload: dict) -> dict:
    """Drop trailing PR lines only if the card would exceed the Teams limit.

    Teams rejects oversized cards outright, so an unusually busy day would
    otherwise lose the entire announcement rather than a few list entries.
    """
    if len(json.dumps(payload).encode("utf-8")) <= MAX_PAYLOAD_BYTES:
        return payload

    body = payload["attachments"][0]["content"]["body"]
    pr_indexes = [i for i, b in enumerate(body) if is_pr_line(b)]
    if not pr_indexes:
        log(f"Card is {len(json.dumps(payload).encode()):,} bytes with no PR lines "
            f"to drop; Teams may reject it.")
        return payload

    first_pr = pr_indexes[0]

    def note(count: int) -> dict:
        return {
            "type": "TextBlock",
            "text": f"➕ _{count} more not shown — the card hit the Teams size limit._",
            "wrap": True,
            "isSubtle": True,
            "spacing": "Small",
        }

    def size() -> int:
        return len(json.dumps(payload).encode("utf-8"))

    # The note itself costs bytes, so it has to be measured as part of each
    # candidate rather than appended once trimming is already finished.
    dropped = 0
    while pr_indexes:
        body.pop(pr_indexes.pop())
        dropped += 1
        insert_at = (pr_indexes[-1] + 1) if pr_indexes else first_pr
        body.insert(insert_at, note(dropped))
        if size() <= MAX_PAYLOAD_BYTES:
            log(f"Card exceeded {MAX_PAYLOAD_BYTES:,} bytes; trimmed {dropped} PR line(s).")
            return payload
        body.pop(insert_at)

    log(f"Card still over {MAX_PAYLOAD_BYTES:,} bytes with every PR line removed.")
    body.insert(first_pr, note(dropped))
    return payload


def envelope(body: list[dict], day: str, cfg: dict) -> dict:
    """Wrap the body in the message shape Power Automate expects.

    A bare {"text": ...} payload returns HTTP 202 and then silently fails inside
    the flow run, so the full Adaptive Card attachment is mandatory.
    """
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": {
                    "type": "AdaptiveCard",
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "version": "1.4",
                    "msteams": {"width": "Full"},
                    "body": body,
                    "actions": [
                        {
                            "type": "Action.OpenUrl",
                            "title": f"🔗 Open {CONFIG['pr_repo'].split('/')[-1]} pull requests",
                            "url": f"https://github.com/{CONFIG['pr_repo']}/pulls",
                        }
                    ],
                },
            }
        ],
    }


def post(payload: dict) -> None:
    webhook = os.environ.get("TEAMS_WEBHOOK_URL", "").strip()
    if not webhook:
        sys.exit("TEAMS_WEBHOOK_URL is not set.")

    data = json.dumps(payload).encode("utf-8")
    log(f"Payload is {len(data):,} bytes.")
    try:
        # Request() itself raises ValueError on a URL with no scheme, so it has
        # to be constructed inside the guard rather than above it.
        req = urllib.request.Request(
            webhook,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            # 202 means Power Automate accepted the request, NOT that the card
            # rendered. A malformed payload still fails later inside the flow
            # run, so check the flow history when a post does not appear.
            log(f"Teams accepted the card: HTTP {resp.status}.")
    except urllib.error.HTTPError as exc:
        sys.exit(f"Teams rejected the card: HTTP {exc.code} — {exc.read().decode('utf-8', 'replace')[:500]}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        # An unreachable or misconfigured webhook should read as a clear failure
        # in the Actions log, not a traceback. ValueError covers a URL with no
        # scheme, which Request() rejects before any network call happens.
        sys.exit(f"Could not reach the Teams webhook: {type(exc).__name__}: {exc}")


def main() -> None:
    day = resolve_day()
    if day is None:
        return

    prs, pr_error = fetch_open_prs()
    if pr_error:
        log(f"Continuing without the PR section: {pr_error}")
    else:
        log(f"Fetched {len(prs)} open pull request(s).")

    payload = trim_to_fit(build_card(day, prs, pr_error))

    if os.environ.get("DRY_RUN") == "1":
        log("DRY_RUN=1, printing payload instead of posting:")
        log(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    post(payload)


if __name__ == "__main__":
    main()
