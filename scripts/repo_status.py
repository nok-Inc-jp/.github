"""Organization のリポジトリの動きを集めて、README の決まった場所と活動のグラフ（SVG）に書き込む。

使い方:
    GH_TOKEN=... python3 scripts/repo_status.py --org nok-Inc-jp --readme profile/README.md
    python3 scripts/repo_status.py --fixture tests/fixture.json --readme profile/README.md  # 試し

ACTIVITY_TOKEN（非公開のリポジトリも読める鍵）を渡したときだけ、非公開の分を「数だけ」グラフに足す。
非公開のリポジトリの名前・PR の題・コミットの中身は、README にも SVG にも出さない。

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

STATUS = ("<!-- repo-status:start -->", "<!-- repo-status:end -->")
ACTIVITY = ("<!-- activity:start -->", "<!-- activity:end -->")
JST = timezone(timedelta(hours=9))
WEEKS = 12
FEED = 5
# このページ自身。bot が書き換えるたびに push の日が変わり、それがまた表を変えてしまうので、表にも数にも入れない。
SELF = ".github"

REPOS_QUERY = """
query($org: String!, $after: String, $privacy: RepositoryPrivacy!) {
  organization(login: $org) {
    repositories(first: 100, after: $after, privacy: $privacy, orderBy: {field: PUSHED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        name
        url
        description
        isPrivate
        isArchived
        isFork
        pushedAt
        pullRequests(states: OPEN) { totalCount }
        issues(states: OPEN) { totalCount }
        latestRelease { tagName publishedAt }
        releases(first: 5, orderBy: {field: CREATED_AT, direction: DESC}) {
          nodes { tagName url publishedAt isDraft }
        }
        defaultBranchRef {
          name
          target {
            ... on Commit { statusCheckRollup { state } }
          }
        }
      }
    }
  }
}
"""

COMMITS_QUERY = """
query($owner: String!, $name: String!, $since: GitTimestamp!, $after: String) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef {
      target {
        ... on Commit {
          history(since: $since, first: 100, after: $after) {
            pageInfo { hasNextPage endCursor }
            nodes { committedDate author { name email } }
          }
        }
      }
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
        title
        url
        mergedAt
        author { __typename login }
        repository { name isPrivate }
      }
    }
  }
}
"""

# CI の状態。色だけにしないよう、記号と言葉を必ず添える。
CI_LABEL = {
    "SUCCESS": "✅ 通過",
    "FAILURE": "❌ 失敗",
    "ERROR": "❌ 失敗",
    "PENDING": "🔄 動いている",
    "EXPECTED": "🔄 動いている",
}

# ロゴとサイトと同じ色（nigoh/nok の public/brand/ と src/styles/globals.css）。
# PR の公開分はロゴの扉の青、非公開分は控えめな灰青（どちらも地に対して 3:1 以上）。
PALETTE = {
    "light": {"bg": "#fafbfe", "line": "#dbe2ec", "fg": "#0f2b4c", "muted": "#617083", "public": "#1565c0", "private": "#78899f"},
    "dark": {"bg": "#10294a", "line": "#24476f", "fg": "#fafbfe", "muted": "#a9bfdd", "public": "#7cc0ff", "private": "#78899f"},
}
MONO = "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace"


# ---- 集める ----------------------------------------------------------------


def gql(token: str, query: str, variables: dict) -> dict:
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "repo-status",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        data = json.load(res)
    if data.get("errors"):
        raise SystemExit("GitHub が誤りを返しました: " + json.dumps(data["errors"], ensure_ascii=False))
    return data["data"]


def fetch_repos(org: str, token: str, privacy: str) -> list[dict]:
    repos: list[dict] = []
    after = None
    while True:
        page = gql(token, REPOS_QUERY, {"org": org, "after": after, "privacy": privacy})["organization"]["repositories"]
        repos.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            return repos
        after = page["pageInfo"]["endCursor"]


def fetch_commits(org: str, name: str, since: datetime, token: str) -> list[dict]:
    commits: list[dict] = []
    after = None
    while True:
        repo = gql(token, COMMITS_QUERY, {"owner": org, "name": name, "since": since.isoformat(), "after": after})["repository"]
        target = ((repo or {}).get("defaultBranchRef") or {}).get("target") or {}
        history = target.get("history")
        if not history:  # 空のリポジトリ
            return commits
        commits.extend(history["nodes"])
        if not history["pageInfo"]["hasNextPage"]:
            return commits
        after = history["pageInfo"]["endCursor"]


def fetch_pulls(query: str, token: str) -> list[dict]:
    pulls: list[dict] = []
    after = None
    while True:
        page = gql(token, PULLS_QUERY, {"q": query, "after": after})["search"]
        pulls.extend(n for n in page["nodes"] if n)
        if not page["pageInfo"]["hasNextPage"]:
            return pulls
        after = page["pageInfo"]["endCursor"]


def collect(org: str, token: str, since: datetime, private_token: str | None = None) -> dict:
    """fixture.json と同じ形にまとめる。非公開の分は private_token があるときだけ読む。"""
    repos = fetch_repos(org, token, "PUBLIC")
    if private_token:
        repos += fetch_repos(org, private_token, "PRIVATE")
    names = counted(repos, include_private=bool(private_token))
    commits = {}
    for r in repos:
        if r["name"] in names["public"] | names["private"]:
            commits[r["name"]] = fetch_commits(org, r["name"], since, private_token or token)
    q = f"org:{org} is:pr is:merged merged:>={(since - timedelta(days=1)):%Y-%m-%d}"
    if not private_token:
        q += " is:public"
    pulls = fetch_pulls(q, private_token or token)
    return {"private": bool(private_token), "repos": repos, "commits": commits, "pulls": pulls}


# ---- 数える ----------------------------------------------------------------


def parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def week_start(t: datetime) -> datetime:
    """JST の月曜 0 時。"""
    j = t.astimezone(JST).replace(hour=0, minute=0, second=0, microsecond=0)
    return j - timedelta(days=j.weekday())


def window_start(now: datetime) -> datetime:
    return week_start(now) - timedelta(weeks=WEEKS - 1)


def is_bot(*names: str | None) -> bool:
    return any(n and "[bot]" in n for n in names)


def live(r: dict) -> bool:
    return not r["isArchived"] and not r["isFork"] and r["name"] != SELF


def counted(repos: list[dict], include_private: bool) -> dict[str, set[str]]:
    return {
        "public": {r["name"] for r in repos if not r["isPrivate"] and live(r)},
        "private": {r["name"] for r in repos if r["isPrivate"] and live(r)} if include_private else set(),
    }


def activity(data: dict, now: datetime) -> dict:
    include_private = bool(data.get("private"))
    names = counted(data["repos"], include_private)
    start = window_start(now)
    public = [0] * WEEKS
    private = [0] * WEEKS
    days: set = set()
    commits = 0
    feed = []

    def slot(t: datetime) -> int | None:
        i = (week_start(t) - start).days // 7
        return i if 0 <= i < WEEKS else None

    for name, items in data["commits"].items():
        if name not in names["public"] | names["private"]:
            continue
        for c in items:
            author = c.get("author") or {}
            if is_bot(author.get("name"), author.get("email")):
                continue
            t = parse(c["committedDate"])
            if slot(t) is None:
                continue
            commits += 1
            days.add(t.astimezone(JST).date())

    for p in data["pulls"]:
        author = p.get("author") or {}
        if author.get("__typename") == "Bot" or is_bot(author.get("login")):
            continue
        repo = p["repository"]
        t = parse(p["mergedAt"])
        i = slot(t)
        if i is None:
            continue
        if repo["name"] in names["public"] and not repo["isPrivate"]:
            public[i] += 1
            feed.append((t, repo["name"], p["title"], p["url"], False))
        elif repo["name"] in names["private"]:
            private[i] += 1  # 数だけ。名前も題も持ち出さない
        else:
            continue
        days.add(t.astimezone(JST).date())

    for r in data["repos"]:
        if r["name"] not in names["public"]:
            continue
        for rel in ((r.get("releases") or {}).get("nodes")) or []:
            if rel.get("isDraft") or not rel.get("publishedAt"):
                continue
            t = parse(rel["publishedAt"])
            if slot(t) is not None:
                feed.append((t, r["name"], rel["tagName"], rel["url"], True))

    feed.sort(key=lambda x: x[0], reverse=True)
    return {
        "include_private": include_private,
        "weeks": [start + timedelta(weeks=i) for i in range(WEEKS)],
        "public": public,
        "private": private,
        "prs": sum(public) + sum(private),
        "prs_public": sum(public),
        "commits": commits,
        "days": len(days),
        "feed": feed[:FEED],
    }


# ---- 書く：表 --------------------------------------------------------------


def day(iso: str | None) -> str:
    """日付だけを出す。「N時間前」にすると毎回表が変わり、意味のないコミットが増えるため。"""
    if not iso:
        return "—"
    return parse(iso).astimezone(JST).strftime("%Y-%m-%d")


def ci_of(repo: dict) -> str:
    ref = repo.get("defaultBranchRef") or {}
    rollup = ((ref.get("target") or {}).get("statusCheckRollup")) or {}
    return CI_LABEL.get(rollup.get("state") or "", "— なし")


def esc(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def render(repos: list[dict], now: datetime) -> str:
    # 公開トップに出すので、非公開のものは問い合わせでも表でも外す（二重の守り）
    repos = [r for r in repos if not r["isPrivate"] and r["name"] != SELF]
    shown = [r for r in repos if not r["isArchived"] and not r["isFork"]]
    archived = [r for r in repos if r["isArchived"]]
    failing = [r for r in shown if ci_of(r).startswith("❌")]
    prs = sum(r["pullRequests"]["totalCount"] for r in shown)

    lines = [
        f"リポジトリ **{len(shown)}** · 開いている PR **{prs}** · CI が失敗 **{len(failing)}**"
        f"　<sub>表が変わった時刻 {now.astimezone(JST):%Y-%m-%d %H:%M} JST</sub>",
        "",
        "| リポジトリ | 最後の push | PR | Issue | CI（既定ブランチ） | 最新の版 |",
        "| --- | --- | ---: | ---: | --- | --- |",
    ]
    for r in shown:
        rel = r.get("latestRelease")
        desc = f"<br><sub>{esc(r['description'])}</sub>" if r.get("description") else ""
        lines.append(
            "| [{name}]({url}){desc} | {pushed} | {pr} | {issue} | {ci} | {rel} |".format(
                name=r["name"],
                url=r["url"],
                desc=desc,
                pushed=day(r.get("pushedAt")),
                pr=r["pullRequests"]["totalCount"],
                issue=r["issues"]["totalCount"],
                ci=ci_of(r),
                rel=f"`{rel['tagName']}`" if rel else "—",
            )
        )
    if archived:
        names = "、".join(f"[{r['name']}]({r['url']})" for r in archived)
        lines += ["", f"<sub>アーカイブ済み：{names}</sub>"]
    return "\n".join(lines)


# ---- 書く：活動 ------------------------------------------------------------


def md(text: str, limit: int = 60) -> str:
    """PR の題を、Markdown の飾りや HTML として読まれないように一行の文字にする。"""
    t = " ".join(text.split())
    if len(t) > limit:
        t = t[: limit - 1].rstrip() + "…"
    t = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_\[\]|~])", r"\\\1", t)


def md_day(t: datetime) -> str:
    return f"{t.astimezone(JST):%m/%d}"


def alt_text(act: dict) -> str:
    totals = [a + b for a, b in zip(act["public"], act["private"])]
    scope = f"非公開のリポジトリを含む。うち公開 {act['prs_public']} 件" if act["include_private"] else "公開リポジトリ"
    text = f"直近{WEEKS}週にマージした PR を週ごとに数えた棒グラフ（{scope}）。合計 {act['prs']} 件"
    if act["prs"]:
        peak = max(range(WEEKS), key=lambda i: (totals[i], i))
        text += f"、いちばん多いのは {md_day(act['weeks'][peak])} からの週の {totals[peak]} 件"
    return text + f"。コミット {act['commits']} 件、手を動かした日 {act['days']} 日。"


def render_activity(act: dict) -> str:
    if act["include_private"]:
        note = "非公開のリポジトリも、数だけ入れています（名前や中身は出しません）。bot の分は入れていません。"
    else:
        note = "公開リポジトリの既定ブランチを数えています。bot の分は入れていません。"
    lines = [
        "<picture>",
        '  <source media="(prefers-color-scheme: dark)" srcset="assets/activity-dark.svg">',
        f'  <img src="assets/activity-light.svg" alt="{alt_text(act)}" width="100%">',
        "</picture>",
        "",
        f"<sub>{note}</sub>",
    ]
    if act["feed"]:
        lines += ["", "**最近の動き**", ""]
        for t, repo, title, url, release in act["feed"]:
            what = f"🏷️ [{md(title)}]({url}) を公開" if release else f"[{md(title)}]({url})"
            lines.append(f"- {md_day(t)} · **{repo}** · {what}")
    return "\n".join(lines)


def svg(act: dict, theme: str) -> str:
    c = PALETTE[theme]
    totals = [a + b for a, b in zip(act["public"], act["private"])]
    top = max(totals) or 1
    x0, x1, base, tall = 430, 1150, 228, 130
    step = (x1 - x0) / WEEKS
    bar = 38

    def text(x, y, s, fill, size, weight=400, anchor="start", spacing=0):
        return (
            f'<text x="{x:g}" y="{y:g}" fill="{fill}" font-family="{MONO}" font-size="{size}"'
            f' font-weight="{weight}" text-anchor="{anchor}" letter-spacing="{spacing}">{s}</text>'
        )

    def stat(y, n, label):
        return (
            f'<text x="64" y="{y}" font-family="{MONO}">'
            f'<tspan fill="{c["fg"]}" font-size="28" font-weight="700">{n:,}</tspan>'
            f'<tspan fill="{c["muted"]}" font-size="14" letter-spacing="2" dx="12">{label}</tspan></text>'
        )

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="300" viewBox="0 0 1200 300" role="img" aria-label="{alt_text(act)}">',
        f'  <rect x="0.5" y="0.5" width="1199" height="299" rx="14" fill="{c["bg"]}" stroke="{c["line"]}"/>',
        "  " + text(64, 80, f"LAST {WEEKS} WEEKS", c["muted"], 16, spacing=3),
        "  " + text(64, 162, f"{act['prs']:,}", c["public"], 76, weight=700),
        "  " + text(66, 194, "PULL REQUESTS MERGED", c["muted"], 15, spacing=2),
        "  " + stat(240, act["commits"], "COMMITS"),
        "  " + stat(274, act["days"], "ACTIVE DAYS"),
        "  " + text(x0, 56, "MERGED PRS PER WEEK", c["muted"], 14, spacing=2),
    ]
    if act["include_private"]:
        out += [
            f'  <rect x="900" y="45" width="12" height="12" rx="2" fill="{c["public"]}"/>',
            "  " + text(920, 56, "PUBLIC", c["muted"], 14, spacing=2),
            f'  <rect x="1010" y="45" width="12" height="12" rx="2" fill="{c["private"]}"/>',
            "  " + text(1030, 56, "PRIVATE", c["muted"], 14, spacing=2),
        ]
    out.append(f'  <rect x="{x0}" y="{base}" width="{x1 - x0}" height="1" fill="{c["line"]}"/>')
    for i in range(WEEKS):
        x = round(x0 + i * step + (step - bar) / 2, 1)
        mid = round(x + bar / 2, 1)
        now = i == WEEKS - 1
        if totals[i]:
            h_pub = round(act["public"][i] / top * tall, 1)
            h_all = round(totals[i] / top * tall, 1)
            # 棒全体の形で切り抜き、公開と非公開の境目は平らにする
            out += [
                f'  <clipPath id="w{i}"><rect x="{x}" y="{base - h_all}" width="{bar}" height="{h_all}" rx="5"/></clipPath>',
                f'  <g clip-path="url(#w{i})">',
            ]
            if act["private"][i]:
                out.append(f'    <rect x="{x}" y="{base - h_all}" width="{bar}" height="{h_all - h_pub}" fill="{c["private"]}"/>')
            if act["public"][i]:
                out.append(f'    <rect x="{x}" y="{base - h_pub}" width="{bar}" height="{h_pub}" fill="{c["public"]}"/>')
            out += ["  </g>", "  " + text(mid, base - h_all - 10, str(totals[i]), c["fg"], 16, weight=700, anchor="middle")]
        else:
            out.append(f'  <rect x="{x}" y="{base - 3}" width="{bar}" height="3" rx="1.5" fill="{c["line"]}"/>')
        label = f"{act['weeks'][i]:%m/%d}"
        out.append("  " + text(mid, base + 28, label, c["fg"] if now else c["muted"], 15, weight=700 if now else 400, anchor="middle"))
    out.append("</svg>")
    return "\n".join(out) + "\n"


# ---- README を差し替える ---------------------------------------------------

TIME = re.compile(r"<sub>表が変わった時刻 [^<]*</sub>")


def strip_time(text: str) -> str:
    return TIME.sub("", text)


def replace_block(readme: str, markers: tuple[str, str], block: str) -> str:
    start, end = markers
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
    if not pattern.search(readme):
        raise SystemExit(f"README に {start} と {end} の印がありません。")
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
    p.add_argument("--assets", help="活動の SVG を置く場所（既定は README の隣の assets/）")
    p.add_argument("--fixture", help="GitHub を呼ばず、この JSON を使う")
    p.add_argument("--now", help="試し用の現在時刻（ISO 8601）")
    a = p.parse_args(argv)

    now = datetime.fromisoformat(a.now) if a.now else datetime.now(timezone.utc)
    if a.fixture:
        with open(a.fixture, encoding="utf-8") as f:
            data = json.load(f)
    else:
        token = os.environ.get("GH_TOKEN")
        if not token:
            print("GH_TOKEN がありません。Actions では secrets.GITHUB_TOKEN を渡してください。", file=sys.stderr)
            return 1
        data = collect(a.org, token, window_start(now), os.environ.get("ACTIVITY_TOKEN") or None)

    act = activity(data, now)
    assets = a.assets or os.path.join(os.path.dirname(a.readme), "assets")
    os.makedirs(assets, exist_ok=True)
    for theme in PALETTE:
        write_if_changed(os.path.join(assets, f"activity-{theme}.svg"), svg(act, theme))

    with open(a.readme, encoding="utf-8") as f:
        old = f.read()
    new = replace_block(old, ACTIVITY, render_activity(act))
    new = replace_block(new, STATUS, render(data["repos"], now))
    # 時刻のほかに変わりがなければ書かない（コミットを増やさない）
    write_if_changed(a.readme, new, same=lambda x, y: strip_time(x) == strip_time(y))
    return 0


if __name__ == "__main__":
    sys.exit(main())
