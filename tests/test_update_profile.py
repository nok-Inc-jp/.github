import json
import pathlib
import shutil
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import update_profile as up  # noqa: E402

FIXTURE = ROOT / "tests" / "fixture.json"
NOW = "2026-11-27T09:00:00+00:00"  # 日本時間 11/27（金）18:00。今週は 11/23 から
LEAKS = ("secret-tool", "secret-konomi", "秘密")


class UpdateProfileTest(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.readme = self.dir / "README.md"
        shutil.copy(ROOT / "profile" / "README.md", self.readme)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def run_at(self, now=NOW, private=False):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        data["private"] = private
        fixture = self.dir / "fixture.json"
        fixture.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        up.main(["--fixture", str(fixture), "--readme", str(self.readme), "--now", now])
        return self.readme.read_text(encoding="utf-8")

    def svg(self, name, theme="light"):
        return (self.dir / "assets" / f"{name}-{theme}.svg").read_text(encoding="utf-8")

    def summary(self, private=False):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        data["private"] = private
        return up.summarize(data, up.parse(NOW))

    def test_counts_public_only(self):
        s = self.summary()
        # bot・非公開・このページ自身・12週より前は数えない。週の区切りは日本時間の月曜
        self.assertEqual(s["public"][-3:], [1, 2, 1])  # 11/22 23:30（日）は前の週、11/23 0:30（月）は今週
        self.assertEqual(sum(s["private"]), 0)
        self.assertEqual(s["touched"], {"public": 1, "private": 0})  # nok-plugins だけ。アーカイブ済みは数えない

    def test_counts_with_private(self):
        s = self.summary(private=True)
        self.assertEqual(s["private"][-1], 2)
        self.assertEqual(s["touched"], {"public": 1, "private": 1})

    def test_recent(self):
        text = self.run_at()
        recent = [line for line in text.splitlines() if line.startswith("- ")]
        self.assertEqual(
            recent,
            [
                "- 11/23 · **nok-pipe-in** · [パイプを足す](https://github.com/nok-Inc-jp/nok-pipe-in/pull/160)",
                "- 11/21 · **nok-plugins** · [fushin・toryo・takumi を一覧に足す](https://github.com/nok-Inc-jp/nok-plugins/pull/7)",
                "- 11/10 · **nok-plugins** · [表の \\[縦線\\] \\| を \\*壊さない\\* &lt;b&gt;太字&lt;/b&gt;](https://github.com/nok-Inc-jp/nok-plugins/pull/6)",
            ],
        )  # chore は出さず、feat(...): は外す

    def test_flow_public_only(self):
        text = self.run_at()
        # 公開リポジトリにある pipe-in だけ日付を出し、リンクも本体へ。非公開にあるものは日付を出さず、一覧へリンクする
        self.assertIn("| 実装 | [pipe-in](https://github.com/nok-Inc-jp/nok-pipe-in) |", text)
        self.assertIn("| 11/21 |", text)
        self.assertIn("| 要件定義 | [fushin](https://github.com/nok-Inc-jp/nok-plugins) | 業務をヒアリングし、要件シートにまとめる | — |", text)
        self.assertIn("| その他 | [shinjin](https://github.com/nok-Inc-jp/nok-plugins) | 新しく足したプラグイン | — |", text)
        self.assertNotIn("11/27", self.svg("flow"))
        for leak in LEAKS:
            self.assertNotIn(leak, text)

    def test_flow_with_private(self):
        text = self.run_at(private=True)
        self.assertIn("| その他 | [fude](https://github.com/nok-Inc-jp/nok-plugins) | 原稿をサイト・Zenn・Qiita・note 向けに書き出す | **11/27** |", text)
        self.assertIn("| 実装 | [konomi](https://github.com/nok-Inc-jp/nok-plugins) |", text)
        self.assertIn("| 11/23 |", text)  # 4日前なので強調しない
        flow = self.svg("flow", "dark")
        self.assertIn(">11/27<", flow)
        self.assertIn(">11/23<", flow)  # 実装の段は pipe-in と konomi の新しい方
        self.assertIn('fill="#7cc0ff"/>', flow)  # 3日以内は塗りの札で強調
        for leak in LEAKS:
            self.assertNotIn(leak, text)
            for name in ("flow", "stats", "weeks"):
                for theme in ("light", "dark"):
                    self.assertNotIn(leak, self.svg(name, theme))

    def test_activity_text(self):
        text = self.run_at()
        self.assertIn("直近12週にマージした PR は 4 件（公開リポジトリ）。今週は 1 件。今週更新があったリポジトリは 1 件。", text)
        self.assertIn("公開リポジトリのみ集計しています。", text)
        self.assertNotIn("非公開（件数のみ）", self.svg("weeks"))
        text = self.run_at(private=True)
        self.assertIn("直近12週にマージした PR は 6 件（うち公開リポジトリ 4 件）。今週は 3 件。今週更新があったリポジトリは 2 件。", text)
        self.assertIn("非公開（件数のみ）", self.svg("weeks"))

    def test_svgs_are_well_formed(self):
        import xml.dom.minidom

        self.run_at(private=True)
        for name in ("flow", "stats", "weeks"):
            for theme in ("light", "dark"):
                doc = xml.dom.minidom.parseString(self.svg(name, theme))
                self.assertEqual(doc.documentElement.getAttribute("role"), "img")

    def test_date_only_change_is_not_written(self):
        first = self.run_at("2026-11-27T09:00:00+00:00")
        second = self.run_at("2026-11-27T21:00:00+00:00")  # 日本時間では 11/28。数字は同じ
        self.assertEqual(first, second)

    def test_missing_markers(self):
        self.readme.write_text("no markers", encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.run_at()


if __name__ == "__main__":
    unittest.main()
