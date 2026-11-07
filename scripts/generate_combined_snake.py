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


def cell_level(cell: dict[str, Any]) -> int:
    return max(cell["personal_level"], bot_level(cell["bot"]))


def build_classic_route(
    cells: list[dict[str, Any]],
) -> tuple[list[tuple[int, int]], dict[tuple[int, int], int], list[tuple[int, int]]]:
    food = {
        (cell["x"], cell["y"]): cell
        for cell in cells
        if cell_level(cell) > 0
    }
    if not food:
        return [(-1, 0), (0, 0)], {}, []

    remaining = set(food)
    current = (-1, min(y for _, y in food))
    route = [current]
    eat_steps: dict[tuple[int, int], int] = {}
    food_order: list[tuple[int, int]] = []
    rng = random.Random()

    def move_to(x: int, y: int) -> None:
        nonlocal current
        point = (x, y)
        route.append(point)
        current = point
        if point in remaining:
            eat_steps[point] = len(route) - 1
            food_order.append(point)
            remaining.remove(point)

    while remaining:
        nearest = sorted(
            remaining,
            key=lambda point: (
                abs(point[0] - current[0]) + abs(point[1] - current[1]),
                point[1],
                point[0],
            ),
        )
        target = rng.choice(nearest[: min(6, len(nearest))])

        while current[0] != target[0]:
            move_to(current[0] + (1 if target[0] > current[0] else -1), current[1])

        while current[1] != target[1]:
            move_to(current[0], current[1] + (1 if target[1] > current[1] else -1))

        if current in remaining:
            eat_steps[current] = len(route) - 1
            food_order.append(current)
            remaining.remove(current)

    route.append((current[0] + 1, current[1]))
    return route, eat_steps, food_order


def merge_transform_frames(frames: list[tuple[float, str]]) -> list[tuple[float, str]]:
    merged = []
    for time, style in frames:
        if merged and merged[-1][1] == style:
            continue
        merged.append((time, style))
    if merged:
        merged[-1] = (1.0, merged[-1][1])
    return merged


def css_keyframes(name: str, frames: list[tuple[float, str]]) -> str:
    values = []
    for time, style in frames:
        percent = max(0.0, min(1.0, time)) * 100
        values.append(f"{percent:.2f}%{{{style}}}")
    return f"@keyframes {name}{{{''.join(values)}}}"


def render_svg(cells: list[dict[str, Any]], owner: str, dark: bool) -> str:
    width = max(cell["x"] for cell in cells) + 1
    height = max(cell["y"] for cell in cells) + 1
    size_cell = 16
    size_dot = 12
    dot_margin = (size_cell - size_dot) / 2
    dot_radius = 2

    palette = {
        "border": "#1b1f230a",
        "empty": "#161b22" if dark else "#ebedf0",
        "level1": "#01311f" if dark else "#9be9a8",
        "level2": "#034525" if dark else "#40c463",
        "level3": "#0f6d31" if dark else "#30a14e",
        "level4": "#00c647" if dark else "#216e39",
        "snake": "#800080",
    }

    route, eat_steps, food_order = build_classic_route(cells)
    route_duration = max(12, min(ANIMATION_SECONDS, len(route) * 0.055))
    route_last = max(1, len(route) - 1)
    route_by_point = {(cell["x"], cell["y"]): cell for cell in cells}

    svg_width = (width + 2) * size_cell
    svg_height = (height + 5) * size_cell
    elements = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="{-size_cell} {-size_cell * 2} {svg_width} {svg_height}" '
            f'width="{svg_width}" height="{svg_height}">'
        ),
        "<title>Combined GitHub contribution snake</title>",
        (
            "<desc>Classic contribution snake with personal contributions "
            "and github-actions[bot] commits.</desc>"
        ),
        "<style>",
        (
            ":root{"
            f"--cb:{palette['border']};--ce:{palette['empty']};"
            f"--c1:{palette['level1']};--c2:{palette['level2']};"
            f"--c3:{palette['level3']};--c4:{palette['level4']};"
            f"--cs:{palette['snake']};"
            "}"
        ),
        (
            ".c{shape-rendering:geometricPrecision;fill:var(--ce);"
            "stroke-width:1px;stroke:var(--cb);width:12px;height:12px;"
            f"animation:none {route_duration:.2f}s linear infinite"
            "}"
        ),
        (
            ".s{shape-rendering:geometricPrecision;fill:var(--cs);"
            f"animation:none linear {route_duration:.2f}s infinite"
            "}"
        ),
        (
            ".u{transform-origin:0 0;transform:scale(0,1);"
            f"animation:none linear {route_duration:.2f}s infinite"
            "}"
        ),
        (
            "@media(prefers-reduced-motion:reduce){"
            ".c,.s,.u{animation:none!important}.s{display:none}"
            "}"
        ),
    ]

    for point, step in eat_steps.items():
        cell = route_by_point[point]
        level = cell_level(cell)
        progress = max(0.01, min(99.9, step / route_last * 100))
        keyframe = f"e{point[0]}_{point[1]}"
        elements.append(
            f"@keyframes {keyframe}{{"
            f"0%,{max(0, progress - 0.01):.2f}%{{fill:var(--c{level})}}"
            f"{min(100, progress + 0.01):.2f}%,100%{{fill:var(--ce)}}"
            "}"
        )
        elements.append(
            f".{keyframe}{{fill:var(--c{level});animation-name:{keyframe}}}"
        )

    part_sizes = [14.4, 13.0, 11.9, 10.8]
    for part, size in enumerate(part_sizes):
        positions = []
        for step in range(len(route)):
            point = route[max(0, step - part)]
            x = point[0] * size_cell + (size_cell - size) / 2
            y = point[1] * size_cell + (size_cell - size) / 2
            positions.append(
                (step / route_last, f"transform:translate({x:.1f}px,{y:.1f}px)")
            )
        positions = merge_transform_frames(positions)
        radius = min(4.5, 4 * size / size_dot)
        elements.append(css_keyframes(f"s{part}", positions))
        start_x = -size_cell + (size_cell - size) / 2
        start_y = route[0][1] * size_cell + (size_cell - size) / 2
        elements.append(
            f".s.s{part}{{transform:translate({start_x:.1f}px,{start_y:.1f}px);"
            f"animation-name:s{part}}}"
        )

    elements.append("</style>")

    for cell in sorted(cells, key=lambda item: (item["x"], item["y"])):
        x = cell["x"] * size_cell + dot_margin
        y = cell["y"] * size_cell + dot_margin
        level = cell_level(cell)
        if level == 0:
            elements.append(
                f'<rect class="c" x="{x}" y="{y}" rx="{dot_radius}" '
                f'ry="{dot_radius}"/>'
            )
            continue
        point = (cell["x"], cell["y"])
        keyframe = f"e{point[0]}_{point[1]}"
        day = dt.date.fromisoformat(cell["date"]).strftime("%b %d, %Y")
        tooltip = (
            f"{day}: {cell['personal']} personal contribution"
            f"{'' if cell['personal'] == 1 else 's'}; "
            f"{cell['bot']} github-actions[bot] commit"
            f"{'' if cell['bot'] == 1 else 's'}"
        )
        elements.append(
            f'<rect class="c {keyframe}" x="{x}" y="{y}" rx="{dot_radius}" '
            f'ry="{dot_radius}"><title>{html.escape(tooltip)}</title></rect>'
        )

    for part, size in enumerate(part_sizes):
        margin = (size_cell - size) / 2
        radius = min(4.5, 4 * size / size_dot)
        elements.append(
            f'<rect class="s s{part}" x="{margin:.1f}" y="{margin:.1f}" '
            f'width="{size:.1f}" height="{size:.1f}" rx="{radius:.1f}" '
            f'ry="{radius:.1f}"/>'
        )

    if food_order:
        colors = [cell_level(route_by_point[point]) for point in food_order]
        blocks = []
        start = 0
        for index in range(1, len(colors) + 1):
            if index == len(colors) or colors[index] != colors[start]:
                blocks.append((colors[start], start, index))
                start = index

        total_width = width * size_cell
        unit = total_width / max(1, len(food_order))
        for block_index, (level, start, end) in enumerate(blocks):
            x = start * unit
            block_width = (end - start) * unit + 0.6
            start_time = eat_steps[food_order[start]] / route_last
            end_time = eat_steps[food_order[end - 1]] / route_last
            keyframe = f"u{block_index}"
            frames = [
                (0, "transform:scale(0,1)"),
                (start_time, "transform:scale(0,1)"),
                (end_time, "transform:scale(1,1)"),
                (1, "transform:scale(1,1)"),
            ]
            elements.append(css_keyframes(keyframe, frames))
            elements.append(
                f".u.{keyframe}{{fill:var(--c{level});"
                f"animation-name:{keyframe};transform-origin:{x:.1f}px 0}}"
            )
            elements.append(
                f'<rect class="u {keyframe}" x="{x:.1f}" '
                f'y="{(height + 2) * size_cell}" '
                f'width="{block_width:.1f}" height="{size_dot}"/>'
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
