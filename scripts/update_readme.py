#!/usr/bin/env python3
"""Self-updating GitHub profile README.

Runs in GitHub Actions on a schedule. Pulls live data from the GitHub API and
rewrites every block in README.md that sits between
<!-- AUTO:<NAME>:START --> and <!-- AUTO:<NAME>:END --> markers.
Also renders assets/stats.svg and assets/languages.svg so no third-party
card service is needed.

Only the standard library is used, so the workflow needs no installs.
Edit config.json to change what gets featured, hidden or described.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import math
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
USER = os.environ.get("GH_USER") or CONFIG["username"]
TOKEN = os.environ.get("GITHUB_TOKEN", "")
MOCK = os.environ.get("MOCK_DATA")  # path to a JSON file, for offline testing
NOW = dt.datetime.now(dt.timezone.utc)

LANG_COLORS = {
    "Python": "#3572A5", "TypeScript": "#3178c6", "JavaScript": "#f1e05a",
    "Rust": "#dea584", "HTML": "#e34c26", "CSS": "#663399", "Dart": "#00B4AB",
    "Go": "#00ADD8", "Java": "#b07219", "Kotlin": "#A97BFF", "Swift": "#F05138",
    "C++": "#f34b7d", "C": "#555555", "Shell": "#89e051", "Jupyter Notebook": "#DA5B0B",
    "Vue": "#41b883", "Svelte": "#ff3e00", "PHP": "#4F5D95", "Ruby": "#701516",
}


# ─────────────────────────── GitHub API ────────────────────────────
def _req(url: str, data: bytes | None = None) -> object:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": f"{USER}-readme-bot"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def gql(query: str, variables: dict) -> dict:
    body = json.dumps({"query": query, "variables": variables}).encode()
    out = _req("https://api.github.com/graphql", body)
    if "errors" in out:
        raise RuntimeError(out["errors"])
    return out["data"]


def fetch() -> dict:
    if MOCK:
        return json.loads(Path(MOCK).read_text(encoding="utf-8"))
    repos = []
    page = 1
    while True:
        batch = _req(f"https://api.github.com/users/{USER}/repos?per_page=100&page={page}&type=owner")
        repos += batch
        if len(batch) < 100:
            break
        page += 1
    langs = {}
    for r in repos:
        if r["fork"] or r["name"] in CONFIG["hidden"]:
            continue
        try:
            langs[r["name"]] = _req(r["languages_url"])
        except Exception:
            langs[r["name"]] = {}
    events = _req(f"https://api.github.com/users/{USER}/events/public?per_page=60")
    contrib = {}
    try:
        contrib = gql(
            """query($login:String!){ user(login:$login){
                 followers{totalCount}
                 contributionsCollection{
                   contributionCalendar{ totalContributions weeks{ contributionDays{ date contributionCount } } }
                   totalCommitContributions totalPullRequestContributions
                   totalIssueContributions totalPullRequestReviewContributions } } }""",
            {"login": USER},
        )["user"]
    except Exception as e:  # GraphQL needs a token; degrade gracefully
        print("GraphQL unavailable:", e, file=sys.stderr)
    return {"repos": repos, "langs": langs, "events": events, "contrib": contrib}


# ─────────────────────────── Ranking ────────────────────────────
def days_since(iso: str) -> float:
    t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return max((NOW - t).total_seconds() / 86400, 0)


def score(repo: dict, langs: dict) -> float:
    """Higher = more impressive. Curated boosts in config.json sit on top."""
    s = 0.0
    s += repo["stargazers_count"] * 6 + repo["forks_count"] * 4 + repo.get("watchers_count", 0) * 0.5
    code_bytes = sum(langs.get(repo["name"], {}).values())
    s += math.log10(code_bytes + 1) * 3            # substance
    s += 4 if repo.get("description") else -6      # documented
    s += 4 if repo.get("homepage") else 0          # has a live demo
    s += min(len(repo.get("topics") or []), 5)     # tagged
    s += max(0, 10 - days_since(repo["pushed_at"]) / 18)  # recency, fades over ~6 months
    s += CONFIG["boost"].get(repo["name"], 0)
    return round(s, 2)


def visible(repos: list[dict]) -> list[dict]:
    return [
        r for r in repos
        if not r["fork"] and not r.get("archived") and not r.get("private")
        and r["name"] not in CONFIG["hidden"] and r["name"].lower() != USER.lower()
    ]


# ─────────────────────────── Rendering ────────────────────────────
def esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def card(repo: dict, rank: int) -> str:
    cur = CONFIG["curated"].get(repo["name"], {})
    medal = ["🥇", "🥈", "🥉"][rank] if rank < 3 else "🏅"
    title = cur.get("title") or repo["name"].replace("-", " ").replace("_", " ").title()
    tagline = cur.get("tagline") or repo.get("description") or ""
    body = cur.get("body", "")
    bullets = cur.get("bullets", [])
    stack = cur.get("stack") or [l for l in [repo.get("language")] if l] + (repo.get("topics") or [])[:4]
    demo = cur.get("demo") or repo.get("homepage") or ""
    meta = []
    if repo["stargazers_count"]:
        meta.append(f"⭐ {repo['stargazers_count']}")
    if repo["forks_count"]:
        meta.append(f"🍴 {repo['forks_count']}")
    meta.append(f"🕒 updated {repo['pushed_at'][:10]}")

    out = [f'<td width="50%" valign="top">\n',
           f'### {medal} [{esc(title)}]({repo["html_url"]})']
    if tagline:
        out.append(f"**{esc(tagline)}**\n")
    if body:
        out.append(body + "\n")
    for b in bullets:
        out.append(f"- {b}")
    if bullets:
        out.append("")
    if demo:
        out.append(f"[🚀 Live demo →]({demo})\n")
    if stack:
        out.append(" ".join(f"`{esc(s)}`" for s in stack) + "\n")
    out.append(f"<sub>{' · '.join(meta)}</sub>\n")
    out.append("</td>")
    return "\n".join(out)


def featured_block(repos, langs) -> str:
    ranked = sorted(visible(repos), key=lambda r: score(r, langs), reverse=True)[: CONFIG["featured_count"]]
    rows = []
    for i in range(0, len(ranked), 2):
        cells = [card(r, i + j) for j, r in enumerate(ranked[i:i + 2])]
        if len(cells) == 1:
            cells.append('<td width="50%"></td>')
        rows.append("<tr>\n" + "\n".join(cells) + "\n</tr>")
    note = ("> 🤖 Auto-ranked from live GitHub data: code size, stars, forks, docs, "
            "live demos, topics and recent activity.")
    return note + "\n\n<table>\n" + "\n".join(rows) + "\n</table>"


def recent_block(repos) -> str:
    rs = sorted(visible(repos), key=lambda r: r["pushed_at"], reverse=True)[: CONFIG["recent_count"]]
    lines = []
    for r in rs:
        d = esc((r.get("description") or "").strip())
        d = (d[:90] + "…") if len(d) > 90 else d
        lang = f" `{r['language']}`" if r.get("language") else ""
        lines.append(f"- **[{r['name']}]({r['html_url']})**{lang}{' · ' + d if d else ''} <sub>({r['pushed_at'][:10]})</sub>")
    return "\n".join(lines)


def activity_block(events) -> str:
    hidden = set(CONFIG["hidden"])
    out, seen = [], set()
    for e in events:
        repo = e["repo"]["name"]
        short = repo.split("/")[-1]
        if short in hidden or short.lower() == USER.lower():
            continue
        t, p = e["type"], e.get("payload", {})
        link = f"[{repo}](https://github.com/{repo})"
        if t == "PushEvent":
            n = p.get("size") or len(p.get("commits", [])) or 1
            line = f"🔨 Pushed {n} commit{'s' if n != 1 else ''} to {link}"
        elif t == "PullRequestEvent":
            pr = p.get("pull_request", {})
            line = f"🔀 {p.get('action', '').capitalize()} PR [#{pr.get('number')}]({pr.get('html_url')}) in {link}"
        elif t == "CreateEvent" and p.get("ref_type") == "repository":
            line = f"✨ Created {link}"
        elif t == "ReleaseEvent":
            rel = p.get("release", {})
            line = f"🚀 Released [{esc(rel.get('tag_name', ''))}]({rel.get('html_url')}) of {link}"
        elif t == "WatchEvent":
            line = f"⭐ Starred {link}"
        elif t == "IssuesEvent":
            line = f"🐛 {p.get('action', '').capitalize()} an issue in {link}"
        else:
            continue
        if line in seen:
            continue
        seen.add(line)
        out.append(f"{len(out) + 1}. {line} <sub>{e['created_at'][:10]}</sub>")
        if len(out) >= CONFIG["activity_count"]:
            break
    return "\n".join(out) or "_Quiet week. Building something big._"


def lang_totals(langs: dict) -> list[tuple[str, int]]:
    tot: dict[str, int] = {}
    for name, d in langs.items():
        if name in CONFIG["hidden"]:
            continue
        for k, v in d.items():
            if k in CONFIG.get("ignore_languages", []):
                continue
            tot[k] = tot.get(k, 0) + v
    return sorted(tot.items(), key=lambda kv: kv[1], reverse=True)


# ─────────────────────────── SVG cards ────────────────────────────
SVG_HEAD = """<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">
<defs>
 <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#05060f"/><stop offset=".6" stop-color="#14123a"/><stop offset="1" stop-color="#062a3a"/></linearGradient>
 <linearGradient id="ac" x1="0" x2="1"><stop offset="0" stop-color="#00d4ff"/><stop offset="1" stop-color="#8b5cf6"/></linearGradient>
</defs>
<style>
 .t{{font:700 18px 'Segoe UI',Inter,Helvetica,Arial,sans-serif;fill:#00d4ff;letter-spacing:1px}}
 .l{{font:500 13px 'Segoe UI',Inter,Helvetica,Arial,sans-serif;fill:#94a3b8}}
 .v{{font:800 26px 'Segoe UI',Inter,Helvetica,Arial,sans-serif;fill:#fff}}
 .s{{font:500 12px 'Segoe UI',Inter,Helvetica,Arial,sans-serif;fill:#cbd5e1}}
 .f{{opacity:0;animation:fi .6s ease forwards}}
 @keyframes fi{{to{{opacity:1}}}}
</style>
<rect width="{w}" height="{h}" rx="16" fill="url(#bg)" stroke="#00d4ff" stroke-opacity=".25"/>
"""


def stars_or_live(vis):
    stars = sum(r["stargazers_count"] for r in vis)
    if stars >= CONFIG.get("min_stars_to_show", 5):
        return ("Stars Earned", stars)
    return ("Live Demos", sum(1 for r in vis if r.get("homepage")))


def stats_svg(repos, contrib) -> str:
    vis = visible(repos)
    cc = contrib.get("contributionsCollection", {}) if contrib else {}
    cal = cc.get("contributionCalendar", {})
    days = [d for w in cal.get("weeks", []) for d in w["contributionDays"]]
    # current streak
    streak = 0
    for d in reversed(days):
        if d["contributionCount"] > 0:
            streak += 1
        elif streak or d["date"] != NOW.date().isoformat():
            break
    tiles = [
        ("Contributions (1y)", cal.get("totalContributions", "—")),
        ("Commits (1y)", cc.get("totalCommitContributions", "—")),
        ("Pull Requests", cc.get("totalPullRequestContributions", "—")),
        ("Public Repos", len(vis)),
        stars_or_live(vis),
        ("Current Streak", f"{streak}d" if days else "—"),
    ]
    w, h = 820, 230
    s = [SVG_HEAD.format(w=w, h=h), f'<text x="28" y="40" class="t">⚡ LIVE GITHUB STATS</text>',
         f'<text x="{w-28}" y="40" class="l" text-anchor="end">updated {NOW:%d %b %Y}</text>']
    for i, (label, val) in enumerate(tiles):
        col, row = i % 3, i // 3
        x, y = 28 + col * 262, 62 + row * 80
        s.append(f'<g class="f" style="animation-delay:{i*0.12:.2f}s">'
                 f'<rect x="{x}" y="{y}" width="246" height="66" rx="12" fill="#ffffff" fill-opacity=".04" stroke="url(#ac)" stroke-opacity=".5"/>'
                 f'<text x="{x+18}" y="{y+38}" class="v">{esc(f"{val:,}" if isinstance(val, int) else str(val))}</text>'
                 f'<text x="{x+18}" y="{y+56}" class="l">{esc(label)}</text></g>')
    # sparkline of last 26 weeks
    weeks = cal.get("weeks", [])[-26:]
    if weeks:
        vals = [sum(d["contributionCount"] for d in wk["contributionDays"]) for wk in weeks]
        mx = max(vals) or 1
        pts = " ".join(f"{330 + i*9.6:.1f},{44 - v/mx*20:.1f}" for i, v in enumerate(vals))
        s.append(f'<polyline points="{pts}" fill="none" stroke="url(#ac)" stroke-width="2" stroke-linejoin="round" opacity=".9"/>')
        s.append(f'<text x="{330 + 25*9.6:.0f}" y="57" class="l" text-anchor="end" style="font-size:10px">last 26 weeks</text>')
    s.append("</svg>")
    return "\n".join(s)


def languages_svg(totals) -> str:
    top = totals[: CONFIG.get("language_count", 8)]
    total = sum(v for _, v in top) or 1
    w, h = 820, 70 + ((len(top) + 1) // 2) * 30
    s = [SVG_HEAD.format(w=w, h=h), '<text x="28" y="40" class="t">🧬 LANGUAGES ACROSS MY REPOS</text>']
    x = 28
    bar_w = w - 56
    s.append(f'<clipPath id="r"><rect x="28" y="54" width="{bar_w}" height="12" rx="6"/></clipPath><g clip-path="url(#r)">')
    for name, v in top:
        seg = bar_w * v / total
        s.append(f'<rect x="{x:.1f}" y="54" width="{seg:.1f}" height="12" fill="{LANG_COLORS.get(name, "#8b5cf6")}"/>')
        x += seg
    s.append("</g>")
    for i, (name, v) in enumerate(top):
        cx, cy = 34 + (i % 2) * 400, 92 + (i // 2) * 28
        s.append(f'<g class="f" style="animation-delay:{i*0.08:.2f}s"><circle cx="{cx}" cy="{cy-4}" r="6" fill="{LANG_COLORS.get(name, "#8b5cf6")}"/>'
                 f'<text x="{cx+14}" y="{cy}" class="s">{esc(name)}  <tspan fill="#64748b">{v/total*100:.1f}%</tspan></text></g>')
    s.append("</svg>")
    return "\n".join(s)


# ─────────────────────────── README patching ────────────────────────────
def patch(readme: str, name: str, content: str) -> str:
    pat = re.compile(rf"(<!-- AUTO:{name}:START -->)(.*?)(<!-- AUTO:{name}:END -->)", re.S)
    if not pat.search(readme):
        print(f"marker {name} not found, skipping", file=sys.stderr)
        return readme
    return pat.sub(lambda m: f"{m.group(1)}\n{content}\n{m.group(3)}", readme)


def main() -> None:
    data = fetch()
    repos, langs, events, contrib = data["repos"], data["langs"], data["events"], data.get("contrib", {})
    (ROOT / "assets").mkdir(exist_ok=True)
    (ROOT / "assets/stats.svg").write_text(stats_svg(repos, contrib), encoding="utf-8")
    (ROOT / "assets/languages.svg").write_text(languages_svg(lang_totals(langs)), encoding="utf-8")

    readme_path = ROOT / "README.md"
    readme = readme_path.read_text(encoding="utf-8")
    readme = patch(readme, "FEATURED", featured_block(repos, langs))
    readme = patch(readme, "RECENT", recent_block(repos))
    readme = patch(readme, "ACTIVITY", activity_block(events))
    readme = patch(readme, "UPDATED", f"<sub>🤖 This profile updates itself. Last refreshed {NOW:%d %b %Y, %H:%M} UTC.</sub>")
    readme_path.write_text(readme, encoding="utf-8")

    ranking = sorted(visible(repos), key=lambda r: score(r, langs), reverse=True)
    print("Ranking:")
    for r in ranking:
        print(f"  {score(r, langs):7.2f}  {r['name']}")


if __name__ == "__main__":
    main()
