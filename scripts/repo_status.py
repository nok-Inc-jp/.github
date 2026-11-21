"""Organization の公開リポジトリの状況を集めて、README の決まった場所に表として書き込む。

使い方:
    GH_TOKEN=... python3 scripts/repo_status.py --org nok-Inc-jp --readme profile/README.md
    python3 scripts/repo_status.py --fixture tests/fixture.json --readme profile/README.md  # 試し

標準ライブラリだけで動く。GitHub の GraphQL を1回（100件ごと）呼ぶだけ。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

START = "<!-- repo-status:start -->"
END = "<!-- repo-status:end -->"
JST = timezone(timedelta(hours=9))

QUERY = """
query($org: String!, $after: String) {
  organization(login: $org) {
    repositories(first: 100, after: $after, privacy: PUBLIC, orderBy: {field: PUSHED_AT, direction: DESC}) {
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

# CI の状態。色だけにしないよう、記号と言葉を必ず添える。
CI_LABEL = {
    "SUCCESS": "✅ 通過",
    "FAILURE": "❌ 失敗",
    "ERROR": "❌ 失敗",
    "PENDING": "🔄 動いている",
    "EXPECTED": "🔄 動いている",
}


def fetch(org: str, token: str) -> list[dict]:
    repos: list[dict] = []
    after = None
    while True:
        body = json.dumps({"query": QUERY, "variables": {"org": org, "after": after}}).encode()
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
        page = data["data"]["organization"]["repositories"]
        repos.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            return repos
        after = page["pageInfo"]["endCursor"]


def day(iso: str | None) -> str:
    """日付だけを出す。「N時間前」にすると毎回表が変わり、意味のないコミットが増えるため。"""
    if not iso:
        return "—"
    t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return t.astimezone(JST).strftime("%Y-%m-%d")


def ci_of(repo: dict) -> str:
    ref = repo.get("defaultBranchRef") or {}
    rollup = ((ref.get("target") or {}).get("statusCheckRollup")) or {}
    return CI_LABEL.get(rollup.get("state") or "", "— なし")


def esc(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def render(repos: list[dict], now: datetime) -> str:
    # 公開トップに出すので、非公開のものは問い合わせでも表でも外す（二重の守り）
    repos = [r for r in repos if not r["isPrivate"]]
    live = [r for r in repos if not r["isArchived"] and not r["isFork"]]
    archived = [r for r in repos if r["isArchived"]]
    failing = [r for r in live if ci_of(r).startswith("❌")]
    prs = sum(r["pullRequests"]["totalCount"] for r in live)

    lines = [
        f"リポジトリ **{len(live)}** · 開いている PR **{prs}** · CI が失敗 **{len(failing)}**"
        f"　<sub>表が変わった時刻 {now.astimezone(JST):%Y-%m-%d %H:%M} JST</sub>",
        "",
        "| リポジトリ | 最後の push | PR | Issue | CI（既定ブランチ） | 最新の版 |",
        "| --- | --- | ---: | ---: | --- | --- |",
    ]
    for r in live:
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


TIME = re.compile(r"<sub>表が変わった時刻 [^<]*</sub>")


def strip_time(text: str) -> str:
    return TIME.sub("", text)


def replace_block(readme: str, block: str) -> str:
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
    if not pattern.search(readme):
        raise SystemExit(f"README に {START} と {END} の印がありません。")
    return pattern.sub(lambda _: f"{START}\n{block}\n{END}", readme, count=1)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--org", default="nok-Inc-jp")
    p.add_argument("--readme", default="profile/README.md")
    p.add_argument("--fixture", help="GitHub を呼ばず、この JSON（リポジトリの配列）を使う")
    p.add_argument("--now", help="試し用の現在時刻（ISO 8601）")
    a = p.parse_args(argv)

    now = datetime.fromisoformat(a.now) if a.now else datetime.now(timezone.utc)
    if a.fixture:
        with open(a.fixture, encoding="utf-8") as f:
            repos = json.load(f)
    else:
        token = os.environ.get("GH_TOKEN")
        if not token:
            print("GH_TOKEN がありません。Actions では secrets.GITHUB_TOKEN を渡してください。", file=sys.stderr)
            return 1
        repos = fetch(a.org, token)

    with open(a.readme, encoding="utf-8") as f:
        old = f.read()
    new = replace_block(old, render(repos, now))
    # 時刻のほかに変わりがなければ書かない（コミットを増やさない）
    if strip_time(new) != strip_time(old):
        with open(a.readme, "w", encoding="utf-8") as f:
            f.write(new)
    return 0


if __name__ == "__main__":
    sys.exit(main())
