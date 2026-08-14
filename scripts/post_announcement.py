#!/usr/bin/env python3
"""Post the daily release-cycle announcement to Microsoft Teams.

Runs Monday-Thursday at 07:00 America/Los_Angeles. GitHub cron is UTC-only, so
the workflow fires at both 14:00 and 15:00 UTC and this script gates on the
actual Pacific hour -- that keeps the post at 7am local across DST changes.

Environment:
  TEAMS_WEBHOOK_URL  (required)  Power Automate "when a webhook request is
                                 received" trigger URL for the target channel.
  GH_PAT             (optional)  Token with read access to the private PR repo.
                                 Without it the card still posts, minus the PR
                                 section.
  GITHUB_REPOSITORY  (optional)  owner/repo, used to build raw asset URLs.
  ASSET_REF          (optional)  Branch or commit SHA for asset URLs.
  FORCE_DAY          (optional)  Monday..Thursday, bypasses the weekday gate.
  DRY_RUN            (optional)  "1" prints the payload instead of posting.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
POST_HOUR = 7
REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((REPO_ROOT / "announcements.json").read_text(encoding="utf-8"))

# Teams caps the card payload; keep the PR list from growing without bound.
MAX_PRS_SHOWN = 15
HTTP_TIMEOUT = 30


def log(msg: str) -> None:
    print(msg, flush=True)


def resolve_day() -> str | None:
    """Return the weekday name to post for, or None if we should stay quiet."""
    forced = os.environ.get("FORCE_DAY", "").strip()
    if forced:
        if forced not in CONFIG["days"]:
            sys.exit(f"FORCE_DAY={forced!r} is not one of {list(CONFIG['days'])}")
        log(f"FORCE_DAY set; posting the {forced} announcement.")
        return forced

    now = datetime.now(PACIFIC)
    day = now.strftime("%A")
    log(f"Pacific time is {now:%Y-%m-%d %H:%M %Z} ({day}).")

    if day not in CONFIG["days"]:
        log(f"{day} has no announcement configured. Nothing to do.")
        return None
    if now.hour != POST_HOUR:
        log(f"Hour is {now.hour}, not {POST_HOUR}. This is the off-DST duplicate run; skipping.")
        return None
    return day


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
    try:
        raw = gh_api(f"/repos/{repo}/pulls?state=open&sort=updated&direction=desc&per_page=100", token)
    except urllib.error.HTTPError as exc:
        return [], f"GitHub API returned HTTP {exc.code} for {repo}"
    except Exception as exc:  # noqa: BLE001 - degrade gracefully, never block the post
        return [], f"GitHub API call failed: {exc}"

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
    # Drafts first: those are the ones at risk for the cut.
    prs.sort(key=lambda p: (p["status"] != "Draft", -p["number"]))
    return prs, None


def asset_url(asset: str) -> str:
    owner_repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    ref = os.environ.get("ASSET_REF", "").strip() or "main"
    if not owner_repo:
        # Local preview outside Actions.
        return str((REPO_ROOT / "assets" / asset).as_uri())
    return CONFIG["asset_base"].format(owner_repo=owner_repo, ref=ref) + f"/{asset}"


def build_card(day: str, prs: list[dict], pr_error: str | None) -> dict:
    cfg = CONFIG["days"][day]
    body: list[dict] = [
        {
            "type": "Image",
            "url": asset_url(cfg["asset"]),
            "size": "Stretch",
            "altText": f"{day}: {cfg['title']}",
        },
        {
            "type": "TextBlock",
            "text": cfg["title"],
            "weight": "Bolder",
            "size": "Large",
            "wrap": True,
            "spacing": "Medium",
        },
        {
            "type": "TextBlock",
            "text": cfg["description"],
            "wrap": True,
            "isSubtle": True,
            "spacing": "Small",
        },
    ]

    repo = CONFIG["pr_repo"]
    if pr_error:
        body.append({
            "type": "TextBlock",
            "text": f"_Open pull requests unavailable: {pr_error}._",
            "wrap": True,
            "isSubtle": True,
            "spacing": "Medium",
        })
        return envelope(body, day, cfg)

    drafts = sum(1 for p in prs if p["status"] == "Draft")
    ready = len(prs) - drafts
    body.append({
        "type": "TextBlock",
        "text": f"Open pull requests in {repo} — {ready} open, {drafts} draft",
        "weight": "Bolder",
        "wrap": True,
        "spacing": "Medium",
        "separator": True,
    })

    if not prs:
        body.append({
            "type": "TextBlock",
            "text": "Nothing open. The board is clear.",
            "wrap": True,
            "isSubtle": True,
            "spacing": "Small",
        })
    else:
        for pr in prs[:MAX_PRS_SHOWN]:
            title = pr["title"]
            if len(title) > 90:
                title = title[:87].rstrip() + "…"
            body.append({
                "type": "TextBlock",
                "text": f"**{pr['status']}** · [#{pr['number']}]({pr['url']}) {title} — _{pr['author']}_",
                "wrap": True,
                "spacing": "Small",
            })
        if len(prs) > MAX_PRS_SHOWN:
            body.append({
                "type": "TextBlock",
                "text": f"_…and {len(prs) - MAX_PRS_SHOWN} more._",
                "wrap": True,
                "isSubtle": True,
                "spacing": "Small",
            })

    return envelope(body, day, cfg)


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
                            "title": f"Open {CONFIG['pr_repo'].split('/')[-1]} pull requests",
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
    req = urllib.request.Request(
        webhook,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            log(f"Teams accepted the card: HTTP {resp.status}.")
    except urllib.error.HTTPError as exc:
        sys.exit(f"Teams rejected the card: HTTP {exc.code} — {exc.read().decode('utf-8', 'replace')[:500]}")


def main() -> None:
    day = resolve_day()
    if day is None:
        return

    prs, pr_error = fetch_open_prs()
    if pr_error:
        log(f"Continuing without the PR section: {pr_error}")
    else:
        log(f"Fetched {len(prs)} open pull request(s).")

    payload = build_card(day, prs, pr_error)

    if os.environ.get("DRY_RUN") == "1":
        log("DRY_RUN=1, printing payload instead of posting:")
        log(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    post(payload)


if __name__ == "__main__":
    main()
