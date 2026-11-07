#!/usr/bin/env python3
"""Generate a contribution snake for personal and github-actions[bot] activity."""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import random
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any


API_ROOT = "https://api.github.com"
GRAPHQL_URL = f"{API_ROOT}/graphql"
BOT_LOGIN = "github-actions[bot]"
BOT_EMAIL_MARKER = "github-actions[bot]"
USER_AGENT = "manjithahhu20-profile-snake"
ANIMATION_SECONDS = 22


def request_json(
    url: str,
    *,
    token: str | None = None,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    allow_public_fallback: bool = False,
) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if allow_public_fallback and token and exc.code in (401, 403, 404):
            return request_json(url, method=method, payload=payload)
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API error {exc.code} for {url}: {detail}") from exc


def fetch_official_calendar(owner: str, token: str) -> list[dict[str, Any]]:
    query = """
      query ($login: String!) {
        user(login: $login) {
          contributionsCollection {
            contributionCalendar {
              weeks {
                contributionDays {
                  contributionCount
                  contributionLevel
                  date
                  weekday
                }
              }
            }
          }
        }
      }
    """
    result = request_json(
        GRAPHQL_URL,
        token=token,
        method="POST",
        payload={"query": query, "variables": {"login": owner}},
    )
    if result.get("errors"):
        raise RuntimeError(result["errors"][0].get("message", "GitHub GraphQL failed"))

    levels = {
        "NONE": 0,
        "FIRST_QUARTILE": 1,
        "SECOND_QUARTILE": 2,
        "THIRD_QUARTILE": 3,
        "FOURTH_QUARTILE": 4,
    }
    weeks = result["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    cells = []
    for x, week in enumerate(weeks):
        for day in week["contributionDays"]:
            cells.append(
                {
                    "x": x,
                    "y": int(day["weekday"]),
                    "date": day["date"],
                    "personal": int(day["contributionCount"]),
                    "personal_level": levels.get(day["contributionLevel"], 0),
                    "bot": 0,
                }
            )
    return cells


def fetch_public_repositories(owner: str, token: str | None) -> list[dict[str, Any]]:
    repositories = []
    page = 1
    while True:
        url = (
            f"{API_ROOT}/users/{urllib.parse.quote(owner)}/repos"
            f"?type=owner&sort=updated&per_page=100&page={page}"
        )
        batch = request_json(url, token=token, allow_public_fallback=True)
        repositories.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return repositories


def fetch_repository_commits(
    owner: str,
    repo: str,
    since: str,
    until: str,
    token: str | None,
) -> list[dict[str, Any]]:
    commits = []
    page = 1
    while True:
        query = urllib.parse.urlencode(
            {
                "since": since,
                "until": until,
                "per_page": 100,
                "page": page,
            }
        )
        url = f"{API_ROOT}/repos/{owner}/{repo}/commits?{query}"
        batch = request_json(url, token=token, allow_public_fallback=True)
        if not isinstance(batch, list):
            break
        commits.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return commits


def is_bot_commit(commit: dict[str, Any]) -> bool:
    author = commit.get("author") or {}
    commit_author = (commit.get("commit") or {}).get("author") or {}
    login = str(author.get("login") or "").lower()
    email = str(commit_author.get("email") or "").lower()
    return login == BOT_LOGIN or BOT_EMAIL_MARKER in email


def commit_date(commit: dict[str, Any]) -> str | None:
    commit_data = commit.get("commit") or {}
    author = commit_data.get("author") or {}
    committer = commit_data.get("committer") or {}
    value = author.get("date") or committer.get("date")
    return str(value)[:10] if value else None


def add_bot_contributions(
    cells: list[dict[str, Any]],
    owner: str,
    token: str | None,
) -> tuple[int, int]:
    dates = {cell["date"] for cell in cells}
    ordered_dates = sorted(dates)
    if not ordered_dates:
        return 0, 0

    since = f"{ordered_dates[0]}T00:00:00Z"
    until = f"{ordered_dates[-1]}T23:59:59Z"
    repositories = fetch_public_repositories(owner, token)
    counts: dict[str, int] = defaultdict(int)
    total = 0

    for repository in repositories:
        if repository.get("archived") or repository.get("disabled"):
            continue
        repo_name = repository["name"]
        try:
            commits = fetch_repository_commits(
                owner, repo_name, since, until, token
            )
        except RuntimeError as exc:
            print(f"warning: skipped {repo_name}: {exc}")
            continue

        for commit in commits:
            if not is_bot_commit(commit):
                continue
            day = commit_date(commit)
            if day in dates:
                counts[day] += 1
                total += 1

    for cell in cells:
        cell["bot"] = counts.get(cell["date"], 0)
    return total, len(repositories)


def bot_level(count: int) -> int:
    if count <= 0:
        return 0
    if count == 1:
        return 1
    if count <= 3:
        return 2
    if count <= 7:
        return 3
    return 4


def route_cells(width: int, height: int) -> list[tuple[int, int]]:
    route = []
    for x in range(width):
        y_values = range(height) if x % 2 == 0 else range(height - 1, -1, -1)
        route.extend((x, y) for y in y_values)
    return route


def month_labels(cells: list[dict[str, Any]], width: int) -> list[tuple[int, str]]:
    labels = []
    last_month = None
    for x in range(width):
        week_days = sorted(
            (cell for cell in cells if cell["x"] == x),
            key=lambda cell: cell["date"],
        )
        if not week_days:
            continue
        first = dt.date.fromisoformat(week_days[0]["date"])
        month = first.strftime("%b")
        if month != last_month:
            labels.append((x, month))
            last_month = month
    return labels


def render_svg(cells: list[dict[str, Any]], owner: str, dark: bool) -> str:
    width = max(cell["x"] for cell in cells) + 1
    height = max(cell["y"] for cell in cells) + 1
    cell_size = 13
    step = 17
    left = 42
    top = 34
    right = 18
    bottom = 58
    svg_width = left + width * step + right
    svg_height = top + height * step + bottom

    route = route_cells(width, height)
    route_index = {point: index for index, point in enumerate(route)}
    points = [(-1, 0), *route, (width, height - 1)]

    def point_xy(point: tuple[int, int]) -> tuple[float, float]:
        x, y = point
        return (
            left + x * step + cell_size / 2,
            top + y * step + cell_size / 2,
        )

    path_parts = []
    previous_xy = None
    path_length = 0.0
    for index, point in enumerate(points):
        x, y = point_xy(point)
        path_parts.append(f"{'M' if index == 0 else 'L'}{x:.1f},{y:.1f}")
        if previous_xy:
            path_length += ((x - previous_xy[0]) ** 2 + (y - previous_xy[1]) ** 2) ** 0.5
        previous_xy = (x, y)
    path_data = "".join(path_parts)

    segment_count = len(points) - 1
    route_by_point = {
        (cell["x"], cell["y"]): cell for cell in cells
    }

    palette = {
        "empty": "#161b22" if dark else "#ebedf0",
        "border": "#30363d" if dark else "#d0d7de",
        "level1": "#0f3d2e" if dark else "#bbf7d0",
        "level2": "#166534" if dark else "#4ade80",
        "level3": "#15803d" if dark else "#16a34a",
        "level4": "#22c55e" if dark else "#166534",
        "snake": "#fbbf24" if dark else "#f59e0b",
        "text": "#8b949e" if dark else "#57606a",
    }

    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg_width}" '
        f'height="{svg_height}" viewBox="0 0 {svg_width} {svg_height}">',
        "<title>Combined GitHub contribution snake</title>",
        (
            "<desc>Personal contributions and github-actions[bot] commits "
            "from public default branches over the last year.</desc>"
        ),
        "<style>",
        (
            ":root{"
            f"--ce:{palette['empty']};--cb:{palette['border']};"
            f"--c1:{palette['level1']};--c2:{palette['level2']};"
            f"--c3:{palette['level3']};--c4:{palette['level4']};"
            f"--snake:{palette['snake']};--text:{palette['text']};"
            "}"
        ),
        (
            ".cell{shape-rendering:geometricPrecision;stroke:var(--cb);"
            "stroke-width:.65}.label{font:11px -apple-system,BlinkMacSystemFont,"
            "'Segoe UI',sans-serif;fill:var(--text)}"
        ),
        ".food{stroke:none}",
        (
            ".snake-body{fill:none;stroke:var(--snake);stroke-width:8;"
            "stroke-linecap:round;stroke-linejoin:round}"
        ),
        (
            "@media(prefers-reduced-motion:reduce){.food{animation:none!important}"
            ".snake-body,.snake-head{display:none}}"
        ),
    ]

    food_keyframes = []
    for index, point in enumerate(route):
        cell = route_by_point.get(point)
        if not cell:
            continue
        level = max(cell["personal_level"], bot_level(cell["bot"]))
        if level <= 0:
            continue
        progress = max(0.1, min(99.8, (index + 1) / segment_count * 100))
        keyframe = f"eat{index}"
        food_keyframes.extend(
            [
                (
                    f"@keyframes {keyframe}{{"
                    f"0%,{progress - 0.01:.2f}%{{fill:var(--c{level});opacity:1}}"
                    f"{progress + 0.01:.2f}%,100%{{fill:var(--ce);opacity:1}}"
                    "}"
                ),
                (
                    f".{keyframe}{{animation:{keyframe} "
                    f"{ANIMATION_SECONDS}s linear infinite}}"
                ),
            ]
        )
    elements.extend(food_keyframes)
    elements.append("</style>")

    for label_x, label in month_labels(cells, width):
        x = left + label_x * step
        elements.append(
            f'<text class="label" x="{x}" y="{top - 12}">{html.escape(label)}</text>'
        )

    for y, label in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        elements.append(
            f'<text class="label" x="{left - 8}" y="{top + y * step + 10}" '
            f'text-anchor="end">{label}</text>'
        )

    for cell in sorted(cells, key=lambda item: (item["x"], item["y"])):
        x = left + cell["x"] * step
        y = top + cell["y"] * step
        level = max(cell["personal_level"], bot_level(cell["bot"]))
        if level == 0:
            elements.append(
                f'<rect class="cell" x="{x}" y="{y}" width="{cell_size}" '
                f'height="{cell_size}" rx="2.5" fill="var(--ce)"/>'
            )
            continue
        route_id = route_index[(cell["x"], cell["y"])]
        day = dt.date.fromisoformat(cell["date"]).strftime("%b %d, %Y")
        tooltip = (
            f"{day}: {cell['personal']} personal contribution"
            f"{'' if cell['personal'] == 1 else 's'}; "
            f"{cell['bot']} github-actions[bot] commit"
            f"{'' if cell['bot'] == 1 else 's'}"
        )
        elements.append(
            f'<rect class="cell food eat{route_id}" x="{x}" y="{y}" '
            f'width="{cell_size}" height="{cell_size}" rx="2.5" '
            f'fill="var(--c{level})"><title>{html.escape(tooltip)}</title></rect>'
        )

    elements.append(
        f'<path class="snake-body" d="{path_data}" '
        f'stroke-dasharray="76 {path_length + 76:.1f}">'
        f'<animate attributeName="stroke-dashoffset" from="0" '
        f'to="-{path_length + 76:.1f}" dur="{ANIMATION_SECONDS}s" '
        'repeatCount="indefinite"/></path>'
    )
    elements.append(
        f'<circle class="snake-head" r="5.2" fill="var(--snake)">'
        f'<animateMotion path="{path_data}" dur="{ANIMATION_SECONDS}s" '
        'repeatCount="indefinite"/></circle>'
    )

    legend_y = top + height * step + 22
    elements.append(
        f'<text class="label" x="{left}" y="{legend_y}">Less</text>'
    )
    for level in range(5):
        x = left + 35 + level * (cell_size + 3)
        fill = "var(--ce)" if level == 0 else f"var(--c{level})"
        elements.append(
            f'<rect class="cell" x="{x}" y="{legend_y - 10}" '
            f'width="{cell_size}" height="{cell_size}" rx="2.5" fill="{fill}"/>'
        )
    elements.append(
        f'<text class="label" x="{left + 35 + 5 * (cell_size + 3) + 2}" '
        f'y="{legend_y}">More</text>'
    )
    elements.append(
        f'<text class="label" x="{svg_width - right}" y="{legend_y}" '
        f'text-anchor="end">@{html.escape(owner)} + '
        f'@{html.escape(BOT_LOGIN)}</text>'
    )
    elements.append("</svg>")
    return "".join(elements)


def demo_cells() -> list[dict[str, Any]]:
    rng = random.Random(42)
    today = dt.date.today()
    start = today - dt.timedelta(days=364)
    cells = []
    for index in range(365):
        date = start + dt.timedelta(days=index)
        x = index // 7
        personal = rng.choices([0, 1, 2, 4, 8], [45, 25, 15, 10, 5])[0]
        bot = rng.choices([0, 1, 2, 4, 8], [35, 35, 18, 8, 4])[0]
        level = 0
        if personal:
            level = 1 if personal <= 2 else 2 if personal <= 4 else 3 if personal <= 8 else 4
        cells.append(
            {
                "x": x,
                "y": date.weekday(),
                "date": date.isoformat(),
                "personal": personal,
                "personal_level": level,
                "bot": bot,
            }
        )
    return cells


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", default=os.environ.get("GITHUB_OWNER", "manjithahhu20"))
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"))
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()

    if args.demo:
        cells = demo_cells()
        bot_total = sum(cell["bot"] for cell in cells)
        repo_total = 0
    else:
        if not args.token:
            raise SystemExit("GITHUB_TOKEN is required")
        cells = fetch_official_calendar(args.owner, args.token)
        bot_total, repo_total = add_bot_contributions(cells, args.owner, args.token)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    light = render_svg(cells, args.owner, dark=False)
    dark = render_svg(cells, args.owner, dark=True)
    (args.output_dir / "github-contribution-grid-snake.svg").write_text(
        light, encoding="utf-8"
    )
    (args.output_dir / "github-contribution-grid-snake-dark.svg").write_text(
        dark, encoding="utf-8"
    )

    personal_total = sum(cell["personal"] for cell in cells)
    print(
        f"Rendered {len(cells)} days: {personal_total} personal contributions "
        f"and {bot_total} github-actions[bot] commits"
        + (f" across {repo_total} public repositories" if repo_total else "")
    )


if __name__ == "__main__":
    main()
