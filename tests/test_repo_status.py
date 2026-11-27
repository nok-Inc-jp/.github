import json
import pathlib
import shutil
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import repo_status  # noqa: E402

FIXTURE = ROOT / "tests" / "fixture.json"
NOW = "2026-11-21T03:00:00+00:00"  # JST 11/21（土）12:00。今週は 11/16 から


class RepoStatusTest(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.readme = self.dir / "README.md"
        shutil.copy(ROOT / "profile" / "README.md", self.readme)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def run_at(self, now=NOW, fixture=FIXTURE):
        repo_status.main(["--fixture", str(fixture), "--readme", str(self.readme), "--now", now])
        return self.readme.read_text(encoding="utf-8")

    def svg(self, theme):
        return (self.dir / "assets" / f"activity-{theme}.svg").read_text(encoding="utf-8")

    def with_private(self):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        data["private"] = True
        path = self.dir / "fixture-private.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    def test_table(self):
        text = self.run_at()
        self.assertIn("リポジトリ **2** · 開いている PR **3** · CI が失敗 **1**", text)
        self.assertNotIn("secret-tool", text)  # 非公開は出さない
        self.assertNotIn("| [.github]", text)  # このページ自身は表に入れない
        self.assertIn("| [nok-pipe-in](https://github.com/nok-Inc-jp/nok-pipe-in)", text)
        self.assertIn("✅ 通過", text)
        self.assertIn("❌ 失敗", text)
        self.assertIn("`v0.4.0`", text)
        self.assertIn("プラグインを \\| 配る", text)  # 表を壊さない
        self.assertIn("アーカイブ済み：[old-thing]", text)
        self.assertNotIn("| [old-thing]", text)
        self.assertIn("### すぐ試す", text)  # 印の外は残す

    def test_activity_counts_public_only(self):
        text = self.run_at()
        # bot・非公開・このページ自身・12週より前は数えない。週の区切りは JST の月曜
        self.assertIn("合計 4 件、いちばん多いのは 11/16 からの週の 2 件。コミット 5 件、手を動かした日 5 日。", text)
        self.assertIn("（公開リポジトリ）", text)
        self.assertNotIn("秘密", text)
        self.assertNotIn("Bump", text)
        act = repo_status.activity(json.loads(FIXTURE.read_text(encoding="utf-8")), repo_status.parse(NOW))
        self.assertEqual(act["public"][-3:], [1, 1, 2])  # 11/15 23時（JST）は前の週、11/16 1時は今週
        self.assertEqual(sum(act["private"]), 0)

    def test_feed(self):
        text = self.run_at()
        feed = [line for line in text.splitlines() if line.startswith("- ")]
        self.assertEqual(
            feed,
            [
                "- 11/20 · **nok-pipe-in** · [要件メモから CSV を出す](https://github.com/nok-Inc-jp/nok-pipe-in/pull/40)",
                "- 11/16 · **nok-pipe-in** · [表の \\[縦線\\] \\| を \\*壊さない\\* &lt;b&gt;太字&lt;/b&gt;](https://github.com/nok-Inc-jp/nok-pipe-in/pull/39)",
                "- 11/15 · **nok-plugins** · [プラグインの一覧を足す](https://github.com/nok-Inc-jp/nok-plugins/pull/7)",
                "- 11/03 · **nok-pipe-in** · [最初の版](https://github.com/nok-Inc-jp/nok-pipe-in/pull/12)",
                "- 11/01 · **nok-pipe-in** · 🏷️ [v0.4.0](https://github.com/nok-Inc-jp/nok-pipe-in/releases/tag/v0.4.0) を公開",
            ],
        )

    def test_svg(self):
        self.run_at()
        for theme in ("light", "dark"):
            s = self.svg(theme)
            self.assertTrue(s.startswith("<svg "))
            self.assertIn('role="img"', s)
            self.assertIn(">PULL REQUESTS MERGED<", s)
            self.assertEqual(s.count(">11/16<"), 1)  # 今週のラベル
            self.assertNotIn("PRIVATE", s)  # 非公開を入れないときは凡例も出さない
        self.assertIn("#7cc0ff", self.svg("dark"))
        self.assertIn("#1565c0", self.svg("light"))

    def test_private_counts_only(self):
        text = self.run_at(fixture=self.with_private())
        self.assertIn("非公開のリポジトリを含む。うち公開 4 件）。合計 6 件、いちばん多いのは 11/16 からの週の 4 件", text)
        self.assertIn("コミット 7 件、手を動かした日 7 日。", text)
        for leak in ("secret-tool", "秘密", "secret-v1"):
            self.assertNotIn(leak, text)
            self.assertNotIn(leak, self.svg("light"))
            self.assertNotIn(leak, self.svg("dark"))
        self.assertIn(">PRIVATE<", self.svg("dark"))

    def test_time_only_change_is_not_written(self):
        first = self.run_at("2026-11-21T03:00:00+00:00")
        svg = self.svg("light")
        second = self.run_at("2026-11-21T09:00:00+00:00")
        self.assertEqual(first, second)
        self.assertEqual(svg, self.svg("light"))

    def test_missing_markers(self):
        self.readme.write_text("no markers", encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.run_at()


if __name__ == "__main__":
    unittest.main()
