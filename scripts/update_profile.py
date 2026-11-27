"""GitHub の組織トップ（profile/README.md）を最新のデータで更新する。

GitHub Actions から定期的に実行し、次の3つを作り直す。
- 開発フローの図と一覧（プラグインごとの最終更新日つき）
- 開発状況の数字と、週ごとのマージ数のグラフ
- 最近マージした PR の一覧

使い方:
    GH_TOKEN=... python3 scripts/update_profile.py --org nok-Inc-jp --readme profile/README.md
    python3 scripts/update_profile.py --fixture tests/fixture.json --readme profile/README.md --now 2026-11-27T09:00:00+00:00

ACTIVITY_TOKEN（非公開リポジトリを読めるトークン）があるときは、非公開リポジトリの件数と、
非公開リポジトリにあるプラグインの最終更新日も集計する。非公開リポジトリの名前や PR のタイトルは出力しない。
標準ライブラリだけで動く。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
WEEKS = 12  # グラフに出す週の数（JST の月曜はじまり）
FEED = 5  # 最近の更新に出す件数
RECENT_DAYS = 3  # この日数以内に更新したプラグインを強調する
SELF = ".github"  # このページ自身。bot の更新で数字が動かないよう、集計から外す
MARKETPLACE = ("nok-plugins", ".claude-plugin/marketplace.json")

BLOCKS = {
    "flow": ("<!-- flow:start -->", "<!-- flow:end -->"),
    "activity": ("<!-- activity:start -->", "<!-- activity:end -->"),
    "recent": ("<!-- recent:start -->", "<!-- recent:end -->"),
}

# 開発フローの図に置く工程。プラグインはマーケットプレイスの name で指定する。
STAGES = [
    {"name": "要件定義", "sub": "何を作るか", "plugins": ["fushin"]},
    {"name": "設計", "sub": "どう作るか", "plugins": ["toryo"]},
    {"name": "実装", "sub": "テストから書く", "plugins": ["pipe-in", "konomi"]},
    {"name": "点検", "sub": "セキュリティ", "plugins": ["takumi"]},
]
EXTRAS = {"fude": "記事の執筆", "ranbiki": "スキルの蒸留"}  # 工程の外に置くもの

# 一覧に出す説明。マーケットプレイスの説明は長いので、ここで短くしたものを使う。
# ここに無いプラグインは、マーケットプレイスの説明をそのまま使う。
SUMMARY = {
    "fushin": "業務をヒアリングし、要件シートにまとめる",
    "toryo": "構成・認証・バックアップ・運用を決める",
    "pipe-in": "要件定義から検証までを流す開発パイプライン",
    "konomi": "日本語 UI のフロントエンドを一貫して作る",
    "takumi": "セキュリティを点検してレポートする",
    "fude": "原稿をサイト・Zenn・Qiita・note 向けに書き出す",
    "ranbiki": "上位モデルの解き方を下位モデルで再現する",
}

# 最近の更新に出さない PR（作業的なもの）と、タイトルから外す接頭辞
SKIP_TITLE = re.compile(r"^(chore|ci|build|test|tests|docs|style)(\([^)]*\))?!?:", re.I)
PREFIX = re.compile(r"^(feat|fix|refactor|perf)(\([^)]*\))?!?:\s*", re.I)

# ロゴとサイト（nok-inc.jp）と同じ色。どの文字色も地に対して 4.5:1 以上、図形は 3:1 以上。
PALETTE = {
    "light": {"bg": "#fafbfe", "edge": "#dbe2ec", "box": "#e9eef5", "fg": "#0f2b4c", "sub": "#5b6b80",
              "accent": "#1565c0", "on": "#ffffff", "line": "#dbe2ec", "private": "#78899f"},
    "dark": {"bg": "#10294a", "edge": "#24476f", "box": "#142f56", "fg": "#fafbfe", "sub": "#a9bfdd",
             "accent": "#7cc0ff", "on": "#0b1f38", "line": "#24476f", "private": "#78899f"},
}
JP = "'Hiragino Sans', 'Hiragino Kaku Gothic ProN', 'Noto Sans JP', 'Yu Gothic', Meiryo, sans-serif"
MONO = "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

REPOS_QUERY = """
query($org: String!, $after: String, $privacy: RepositoryPrivacy!) {
  organization(login: $org) {
    repositories(first: 100, after: $after, privacy: $privacy) {
      pageInfo { hasNextPage endCursor }
      nodes { name url isPrivate isArchived isFork pushedAt }
    }
  }
}
"""

PULLS_QUERY = """
query($q: String!, $after: String) {
  search(query: $q, type: ISSUE, first: 100, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        title url mergedAt
        author { __typename login }
        repository { name isPrivate }
      }
    }
  }
}
"""

FILE_QUERY = """
query($owner: String!, $name: String!, $expr: String!) {
  repository(owner: $owner, name: $name) { object(expression: $expr) { ... on Blob { text } } }
}
"""

LAST_COMMIT_QUERY = """
query($owner: String!, $name: String!, $path: String) {
  repository(owner: $owner, name: $name) {
    isPrivate
    defaultBranchRef { target { ... on Commit { history(first: 1, path: $path) { nodes { committedDate } } } } }
  }
}
"""


# ---- 集める ----------------------------------------------------------------


def gql(token: str, query: str, variables: dict, strict: bool = True) -> dict:
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": "update-profile"},
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        data = json.load(res)
    if data.get("errors") and strict:
        raise SystemExit("GitHub API がエラーを返しました: " + json.dumps(data["errors"], ensure_ascii=False))
    return data.get("data") or {}


def paginate(token: str, query: str, variables: dict, pick) -> list[dict]:
    items: list[dict] = []
    after = None
    while True:
        page = pick(gql(token, query, {**variables, "after": after}))
        items.extend(n for n in page["nodes"] if n)
        if not page["pageInfo"]["hasNextPage"]:
            return items
        after = page["pageInfo"]["endCursor"]


def repo_of(url: str) -> tuple[str, str] | None:
    m = re.match(r"https://github\.com/([^/]+)/([^/.]+)", url or "")
    return (m.group(1), m.group(2)) if m else None


def collect(org: str, token: str, since: datetime, private_token: str | None = None) -> dict:
    """tests/fixture.json と同じ形にまとめる。非公開の分は private_token があるときだけ読む。"""
    pick_repos = lambda d: d["organization"]["repositories"]  # noqa: E731
    repos = paginate(token, REPOS_QUERY, {"org": org, "privacy": "PUBLIC"}, pick_repos)
    if private_token:
        repos += paginate(private_token, REPOS_QUERY, {"org": org, "privacy": "PRIVATE"}, pick_repos)

    q = f"org:{org} is:pr is:merged merged:>={(since - timedelta(days=1)):%Y-%m-%d}"
    if not private_token:
        q += " is:public"
    pulls = paginate(private_token or token, PULLS_QUERY, {"q": q}, lambda d: d["search"])

    blob = gql(token, FILE_QUERY, {"owner": org, "name": MARKETPLACE[0], "expr": f"HEAD:{MARKETPLACE[1]}"}, strict=False)
    text = (((blob.get("repository") or {}).get("object")) or {}).get("text")
    plugins = []
    for p in (json.loads(text).get("plugins", []) if text else []):
        src = p.get("source")
        if isinstance(src, dict):
            where, path = repo_of(src.get("url", "")), src.get("path")
        else:  # "./plugins/<name>" のように、マーケットプレイスのリポジトリの中にある
            where, path = (org, MARKETPLACE[0]), (src or "").lstrip("./") or None
        info = {"name": p["name"], "category": p.get("category", ""), "description": p.get("description", ""),
                "repo": None, "isPrivate": True, "updatedAt": None}
        if where:
            # 公開リポジトリは最初から付いてくるトークンで読める。非公開は ACTIVITY_TOKEN があるときだけ。
            for tok in filter(None, [token, private_token]):
                r = (gql(tok, LAST_COMMIT_QUERY, {"owner": where[0], "name": where[1], "path": path}, strict=False)
                     .get("repository"))
                if r:
                    nodes = ((((r.get("defaultBranchRef") or {}).get("target") or {}).get("history") or {}).get("nodes")) or []
                    info.update(repo=f"https://github.com/{where[0]}/{where[1]}", isPrivate=r["isPrivate"],
                                updatedAt=nodes[0]["committedDate"] if nodes else None)
                    break
        plugins.append(info)
    return {"private": bool(private_token), "repos": repos, "pulls": pulls, "plugins": plugins,
            "marketplace": f"https://github.com/{org}/{MARKETPLACE[0]}"}


# ---- 数える ----------------------------------------------------------------


def parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def week_start(t: datetime) -> datetime:
    j = t.astimezone(JST).replace(hour=0, minute=0, second=0, microsecond=0)
    return j - timedelta(days=j.weekday())


def window_start(now: datetime) -> datetime:
    return week_start(now) - timedelta(weeks=WEEKS - 1)


def is_bot(author: dict | None) -> bool:
    a = author or {}
    return a.get("__typename") == "Bot" or "[bot]" in (a.get("login") or "")


def counted(repos: list[dict], include_private: bool) -> dict[str, set[str]]:
    live = [r for r in repos if not r["isArchived"] and not r["isFork"] and r["name"] != SELF]
    return {
        "public": {r["name"] for r in live if not r["isPrivate"]},
        "private": {r["name"] for r in live if r["isPrivate"]} if include_private else set(),
    }


def clean_title(title: str) -> str | None:
    t = " ".join(title.split())
    if SKIP_TITLE.match(t):
        return None
    return PREFIX.sub("", t)


def summarize(data: dict, now: datetime) -> dict:
    include_private = bool(data.get("private"))
    names = counted(data["repos"], include_private)
    start = window_start(now)
    this_week = week_start(now)
    public, private = [0] * WEEKS, [0] * WEEKS
    feed = []

    for p in data["pulls"]:
        if is_bot(p.get("author")):
            continue
        t = parse(p["mergedAt"])
        i = (week_start(t) - start).days // 7
        if not 0 <= i < WEEKS:
            continue
        repo = p["repository"]
        if repo["name"] in names["public"] and not repo["isPrivate"]:
            public[i] += 1
            title = clean_title(p["title"])
            if title:
                feed.append((t, repo["name"], title, p["url"]))
        elif repo["name"] in names["private"]:
            private[i] += 1  # 件数だけ。名前もタイトルも持ち出さない

    touched = {"public": 0, "private": 0}
    for r in data["repos"]:
        side = "public" if r["name"] in names["public"] else "private" if r["name"] in names["private"] else None
        if side and r.get("pushedAt") and parse(r["pushedAt"]) >= this_week:
            touched[side] += 1

    plugins = []
    for p in data.get("plugins", []):
        # 非公開リポジトリの日付は、非公開を集計するときだけ出す（二重の守り）
        show = p.get("updatedAt") and (include_private or not p.get("isPrivate", True))
        t = parse(p["updatedAt"]) if show else None
        url = p["repo"] if p.get("repo") and not p.get("isPrivate", True) else data.get("marketplace", "")
        plugins.append({"name": p["name"], "summary": SUMMARY.get(p["name"]) or p.get("description", ""),
                        "url": url, "updated": t,
                        "fresh": bool(t and (now - t) <= timedelta(days=RECENT_DAYS))})

    feed.sort(key=lambda x: x[0], reverse=True)
    return {
        "include_private": include_private,
        "now": now,
        "weeks": [start + timedelta(weeks=i) for i in range(WEEKS)],
        "this_week": this_week,
        "public": public,
        "private": private,
        "touched": touched,
        "plugins": plugins,
        "feed": feed[:FEED],
    }


# ---- 文字の整形 ------------------------------------------------------------


def xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def md(text: str, limit: int = 60) -> str:
    """Markdown の記号や HTML として解釈されないようにする。"""
    t = " ".join(text.split())
    if len(t) > limit:
        t = t[: limit - 1].rstrip() + "…"
    t = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_\[\]|~])", r"\\\1", t)


def mmdd(t: datetime) -> str:
    return f"{t.astimezone(JST):%m/%d}"


def plugin_map(s: dict) -> dict[str, dict]:
    return {p["name"]: p for p in s["plugins"]}


# ---- 書く：開発フロー ------------------------------------------------------


def flow_rows(s: dict) -> list[tuple[str, dict]]:
    by = plugin_map(s)
    rows = [(st["name"], by[n]) for st in STAGES for n in st["plugins"] if n in by]
    rows += [("その他", by[n]) for n in EXTRAS if n in by]
    placed = {p["name"] for _, p in rows}
    rows += [("その他", p) for p in s["plugins"] if p["name"] not in placed]
    return rows


def flow_alt(s: dict) -> str:
    by = plugin_map(s)
    parts = []
    for st in STAGES:
        names = [n for n in st["plugins"] if n in by]
        dates = [by[n]["updated"] for n in names if by[n]["updated"]]
        tail = f"、{mmdd(max(dates))} 更新" if dates else ""
        parts.append(f"{st['name']}（{'・'.join(names) or 'なし'}{tail}）")
    return ("開発フローの図。" + " → ".join(parts) + "。工程の区切りごとに人がレビューする。")


def render_flow(s: dict) -> str:
    lines = [
        "<picture>",
        '  <source media="(prefers-color-scheme: dark)" srcset="assets/flow-dark.svg">',
        f'  <img src="assets/flow-light.svg" alt="{xml(flow_alt(s))}" width="100%">',
        "</picture>",
        "",
        "| 工程 | プラグイン | 内容 | 更新 |",
        "| --- | --- | --- | --- |",
    ]
    for stage, p in flow_rows(s):
        when = "—"
        if p["updated"]:
            when = f"**{mmdd(p['updated'])}**" if p["fresh"] else mmdd(p["updated"])
        name = md(p["name"], 40)
        link = f"[{name}]({p['url']})" if p["url"] else name
        lines.append(f"| {stage} | {link} | {md(p['summary'], 80)} | {when} |")
    return "\n".join(lines)


def svg_flow(s: dict, theme: str) -> str:
    c = PALETTE[theme]
    by = plugin_map(s)
    W, H = 1200, 470
    box_w, gap, top, box_h = 246, 50, 80, 262
    left = (W - (4 * box_w + 3 * gap)) // 2
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="{xml(flow_alt(s))}">',
        f'  <rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="14" fill="{c["bg"]}" stroke="{c["edge"]}"/>',
        f'  <text x="{W - left}" y="54" fill="{c["sub"]}" font-family="{JP}" font-size="24" font-weight="700" text-anchor="end">工程の区切りごとに人がレビュー</text>',
    ]

    def date_tag(x: float, y: float, p_dates: list[dict], anchor: str = "start") -> list[str]:
        dated = [p for p in p_dates if p["updated"]]
        if not dated:
            return []
        p = max(dated, key=lambda q: q["updated"])
        label = mmdd(p["updated"])
        if p["fresh"]:
            w = 26 + len(label) * 17 + 50
            bx = x if anchor == "start" else x - w
            return [
                f'  <rect x="{bx}" y="{y - 30}" width="{w}" height="42" rx="21" fill="{c["accent"]}"/>',
                f'  <text x="{bx + 16}" y="{y}" font-family="{MONO}" font-size="26" font-weight="700" fill="{c["on"]}">{label}'
                f'<tspan font-family="{JP}" font-size="18" dx="8">更新</tspan></text>',
            ]
        return [
            f'  <text x="{x}" y="{y}" font-family="{MONO}" font-size="26" font-weight="700" fill="{c["accent"]}" text-anchor="{anchor}">{label}'
            f'<tspan font-family="{JP}" font-size="18" font-weight="400" fill="{c["sub"]}" dx="8">更新</tspan></text>',
        ]

    for i, st in enumerate(STAGES):
        x = left + i * (box_w + gap)
        tx = x + 20
        out += [
            f'  <rect x="{x}" y="{top}" width="{box_w}" height="{box_h}" rx="12" fill="{c["box"]}"/>',
            f'  <text x="{tx}" y="{top + 52}" font-family="{MONO}" font-size="28" font-weight="700" fill="{c["accent"]}">{i + 1}</text>',
            f'  <text x="{tx + 32}" y="{top + 54}" font-family="{JP}" font-size="36" font-weight="700" fill="{c["fg"]}">{xml(st["name"])}</text>',
            f'  <text x="{tx}" y="{top + 92}" font-family="{JP}" font-size="22" fill="{c["sub"]}">{xml(st["sub"])}</text>',
        ]
        members = [by[n] for n in st["plugins"] if n in by]
        for k, p in enumerate(members[:2]):
            out.append(f'  <text x="{tx}" y="{top + 146 + k * 36}" font-family="{MONO}" font-size="28" font-weight="700" fill="{c["fg"]}">{xml(p["name"])}</text>')
        tag = date_tag(tx, top + box_h - 24, members)
        if tag:
            out.append(f'  <rect x="{tx}" y="{top + box_h - 72}" width="{box_w - 40}" height="2" fill="{c["line"]}"/>')
            out += tag
        if i < len(STAGES) - 1:
            cx = x + box_w + gap / 2
            cy = top + box_h / 2
            out += [
                f'  <line x1="{cx}" x2="{cx}" y1="{top}" y2="{top + box_h}" stroke="{c["private"]}" stroke-width="2" stroke-dasharray="6 6"/>',
                f'  <circle cx="{cx}" cy="{cy}" r="20" fill="{c["bg"]}" stroke="{c["accent"]}" stroke-width="3"/>',
                f'  <circle cx="{cx}" cy="{cy - 6}" r="5.5" fill="{c["accent"]}"/>',
                f'  <path d="M{cx - 10} {cy + 12} q10 -15 20 0 z" fill="{c["accent"]}"/>',
            ]

    extras = [by[n] for n in EXTRAS if n in by]
    if extras:
        y = top + box_h + 34
        out.append(f'  <text x="{left}" y="{y + 38}" font-family="{JP}" font-size="22" fill="{c["sub"]}">その他</text>')
        pill_x, pill_w = left + 96, (W - 2 * left - 96 - 24) // 2
        for k, p in enumerate(extras[:2]):
            px = pill_x + k * (pill_w + 24)
            out += [
                f'  <rect x="{px}" y="{y}" width="{pill_w}" height="58" rx="29" fill="none" stroke="{c["private"]}" stroke-width="2" stroke-dasharray="7 6"/>',
                f'  <text x="{px + 26}" y="{y + 38}" font-family="{MONO}" font-size="24" font-weight="700" fill="{c["fg"]}">{xml(p["name"])}'
                f'<tspan font-family="{JP}" font-size="20" font-weight="400" fill="{c["sub"]}" dx="14">{xml(EXTRAS[p["name"]])}</tspan></text>',
            ]
            out += date_tag(px + pill_w - 18, y + 39, [p], anchor="end")
    out.append("</svg>")
    return "\n".join(out) + "\n"


# ---- 書く：開発状況 --------------------------------------------------------


def activity_alt(s: dict) -> tuple[str, str]:
    total, pub = sum(s["public"]) + sum(s["private"]), sum(s["public"])
    week = s["public"][-1] + s["private"][-1]
    touched = s["touched"]["public"] + s["touched"]["private"]
    scope = f"（うち公開リポジトリ {pub} 件）" if s["include_private"] else "（公開リポジトリ）"
    stats = (f"開発状況。直近{WEEKS}週にマージした PR は {total} 件{scope}。"
             f"今週は {week} 件。今週更新があったリポジトリは {touched} 件。")
    totals = [a + b for a, b in zip(s["public"], s["private"])]
    weeks = f"週ごとにマージした PR の棒グラフ。{mmdd(s['weeks'][0])} の週から今週までの{WEEKS}週分"
    if total:
        peak = max(range(WEEKS), key=lambda i: (totals[i], i))
        weeks += f"。最も多いのは {mmdd(s['weeks'][peak])} の週の {totals[peak]} 件"
    return stats, weeks + "。"


def svg_stats(s: dict, theme: str) -> str:
    c = PALETTE[theme]
    W, H = 1200, 230
    pub, prv = sum(s["public"]), sum(s["private"])
    tiles = [
        (f"直近{WEEKS}週にマージした PR", pub + prv, (pub, prv),
         "公開リポジトリのみ" if not s["include_private"] else "bot を除く"),
        ("今週マージした PR", s["public"][-1] + s["private"][-1], (s["public"][-1], s["private"][-1]),
         f"{mmdd(s['this_week'])}（月）から"),
        ("今週更新があったリポジトリ", s["touched"]["public"] + s["touched"]["private"],
         (s["touched"]["public"], s["touched"]["private"]), "push があったもの"),
    ]
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="{xml(activity_alt(s)[0])}">',
        f'  <rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="14" fill="{c["bg"]}" stroke="{c["edge"]}"/>',
    ]
    pad, gap = 20, 16
    tw = (W - 2 * pad - 2 * gap) / 3
    for i, (label, n, (a, b), note) in enumerate(tiles):
        x = round(pad + i * (tw + gap), 1)
        tx = x + 24
        out += [
            f'  <rect x="{x}" y="{pad}" width="{tw:.1f}" height="{H - 2 * pad}" rx="10" fill="{c["box"]}"/>',
            f'  <text x="{tx}" y="{pad + 40}" font-family="{JP}" font-size="20" font-weight="700" fill="{c["sub"]}">{xml(label)}</text>',
            f'  <text x="{tx}" y="{pad + 112}" font-family="{MONO}" font-size="68" font-weight="700" fill="{c["accent"]}">{n:,}</text>',
        ]
        if s["include_private"]:
            out.append(f'  <text x="{tx}" y="{pad + 146}" font-family="{JP}" font-size="18" fill="{c["fg"]}">公開 {a:,} · 非公開 {b:,}</text>')
        out.append(f'  <text x="{tx}" y="{pad + 172}" font-family="{JP}" font-size="16" fill="{c["sub"]}">{xml(note)}</text>')
        if s["include_private"] and n:
            bw = tw - 48
            wa = round(bw * a / n, 1)
            out += [
                f'  <rect x="{tx}" y="{pad + 180}" width="{wa}" height="5" fill="{c["accent"]}"/>',
                f'  <rect x="{round(tx + wa, 1)}" y="{pad + 180}" width="{round(bw - wa, 1)}" height="5" fill="{c["private"]}"/>',
            ]
    out.append("</svg>")
    return "\n".join(out) + "\n"


def nice_step(top: int) -> int:
    for step in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000):
        if top / step <= 5:
            return step
    return 10 ** len(str(top))


def svg_weeks(s: dict, theme: str) -> str:
    c = PALETTE[theme]
    W, H = 1200, 400
    totals = [a + b for a, b in zip(s["public"], s["private"])]
    step = nice_step(max(totals) or 1)
    ymax = step * max(1, -(-max(totals) // step))
    x0, x1, base, tall = 96, 1160, 320, 220
    slot = (x1 - x0) / WEEKS
    bar = 46
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="{xml(activity_alt(s)[1])}">',
        f'  <rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="14" fill="{c["bg"]}" stroke="{c["edge"]}"/>',
        f'  <text x="40" y="54" font-family="{JP}" font-size="22" font-weight="700" fill="{c["fg"]}">週ごとにマージした PR</text>',
    ]
    if s["include_private"]:
        out += [
            f'  <rect x="{x1 - 330}" y="38" width="18" height="18" rx="3" fill="{c["accent"]}"/>',
            f'  <text x="{x1 - 304}" y="54" font-family="{JP}" font-size="18" fill="{c["sub"]}">公開</text>',
            f'  <rect x="{x1 - 230}" y="38" width="18" height="18" rx="3" fill="{c["private"]}"/>',
            f'  <text x="{x1 - 204}" y="54" font-family="{JP}" font-size="18" fill="{c["sub"]}">非公開（件数のみ）</text>',
        ]
    v = 0
    while v <= ymax:
        y = base - tall * v / ymax
        out += [
            f'  <rect x="{x0}" y="{y:.1f}" width="{x1 - x0}" height="1" fill="{c["line"]}"/>',
            f'  <text x="{x0 - 14}" y="{y + 6:.1f}" font-family="{MONO}" font-size="16" fill="{c["sub"]}" text-anchor="end">{v}</text>',
        ]
        v += step
    for i in range(WEEKS):
        x = round(x0 + i * slot + (slot - bar) / 2, 1)
        mid = round(x + bar / 2, 1)
        if totals[i]:
            h_all = round(tall * totals[i] / ymax, 1)
            h_pub = round(tall * s["public"][i] / ymax, 1)
            out += [
                f'  <clipPath id="w{i}"><rect x="{x}" y="{base - h_all}" width="{bar}" height="{h_all}" rx="6"/></clipPath>',
                f'  <g clip-path="url(#w{i})">',
            ]
            if s["private"][i]:
                out.append(f'    <rect x="{x}" y="{base - h_all}" width="{bar}" height="{round(h_all - h_pub, 1)}" fill="{c["private"]}"/>')
            if s["public"][i]:
                out.append(f'    <rect x="{x}" y="{base - h_pub}" width="{bar}" height="{h_pub}" fill="{c["accent"]}"/>')
            out += ["  </g>",
                    f'  <text x="{mid}" y="{base - h_all - 10}" font-family="{MONO}" font-size="18" font-weight="700" fill="{c["fg"]}" text-anchor="middle">{totals[i]}</text>']
        now = i == WEEKS - 1
        out.append(f'  <text x="{mid}" y="{base + 32}" font-family="{MONO}" font-size="17" font-weight="{700 if now else 400}" '
                   f'fill="{c["fg"] if now else c["sub"]}" text-anchor="middle">{mmdd(s["weeks"][i])}</text>')
    out.append(f'  <text x="{x1}" y="{base + 62}" font-family="{JP}" font-size="16" fill="{c["sub"]}" text-anchor="end">週は月曜はじまり（日本時間）。右端が今週。</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


STAMP = re.compile(r"最終更新 \d{4}-\d{2}-\d{2}")


def render_activity(s: dict) -> str:
    stats_alt, weeks_alt = activity_alt(s)
    scope = ("非公開リポジトリは件数のみ集計し、名前や内容は表示していません。" if s["include_private"]
             else "公開リポジトリのみ集計しています。")
    return "\n".join([
        "<picture>",
        '  <source media="(prefers-color-scheme: dark)" srcset="assets/stats-dark.svg">',
        f'  <img src="assets/stats-light.svg" alt="{xml(stats_alt)}" width="100%">',
        "</picture>",
        "<picture>",
        '  <source media="(prefers-color-scheme: dark)" srcset="assets/weeks-dark.svg">',
        f'  <img src="assets/weeks-light.svg" alt="{xml(weeks_alt)}" width="100%">',
        "</picture>",
        "",
        f"<sub>各リポジトリの既定ブランチにマージされた PR の数です（bot によるものを除く）。{scope}"
        f"最終更新 {s['now'].astimezone(JST):%Y-%m-%d}</sub>",
    ])


def render_recent(s: dict) -> str:
    if not s["feed"]:
        return "直近の更新はまだありません。"
    return "\n".join(f"- {mmdd(t)} · **{repo}** · [{md(title)}]({url})" for t, repo, title, url in s["feed"])


# ---- README を差し替える ---------------------------------------------------


def replace_block(readme: str, markers: tuple[str, str], block: str) -> str:
    start, end = markers
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
    if not pattern.search(readme):
        raise SystemExit(f"README に {start} と {end} がありません。")
    return pattern.sub(lambda _: f"{start}\n{block}\n{end}", readme, count=1)


def write_if_changed(path: str, text: str, same=lambda a, b: a == b) -> None:
    old = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            old = f.read()
    if old is None or not same(old, text):
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--org", default="nok-Inc-jp")
    p.add_argument("--readme", default="profile/README.md")
    p.add_argument("--assets", help="SVG の出力先（既定は README と同じ階層の assets/）")
    p.add_argument("--fixture", help="GitHub API を呼ばず、この JSON を使う（テスト用）")
    p.add_argument("--now", help="現在時刻の上書き（ISO 8601、テスト用）")
    a = p.parse_args(argv)

    now = datetime.fromisoformat(a.now) if a.now else datetime.now(timezone.utc)
    if a.fixture:
        with open(a.fixture, encoding="utf-8") as f:
            data = json.load(f)
    else:
        token = os.environ.get("GH_TOKEN")
        if not token:
            print("GH_TOKEN が設定されていません。Actions では secrets.GITHUB_TOKEN を渡してください。", file=sys.stderr)
            return 1
        data = collect(a.org, token, window_start(now), os.environ.get("ACTIVITY_TOKEN") or None)

    s = summarize(data, now)
    assets = a.assets or os.path.join(os.path.dirname(a.readme), "assets")
    os.makedirs(assets, exist_ok=True)
    for theme in PALETTE:
        write_if_changed(os.path.join(assets, f"flow-{theme}.svg"), svg_flow(s, theme))
        write_if_changed(os.path.join(assets, f"stats-{theme}.svg"), svg_stats(s, theme))
        write_if_changed(os.path.join(assets, f"weeks-{theme}.svg"), svg_weeks(s, theme))

    with open(a.readme, encoding="utf-8") as f:
        new = f.read()
    new = replace_block(new, BLOCKS["flow"], render_flow(s))
    new = replace_block(new, BLOCKS["activity"], render_activity(s))
    new = replace_block(new, BLOCKS["recent"], render_recent(s))
    # 日付だけが変わった場合は書き込まない（意味のないコミットを増やさない）
    write_if_changed(a.readme, new, same=lambda x, y: STAMP.sub("", x) == STAMP.sub("", y))
    return 0


if __name__ == "__main__":
    sys.exit(main())
