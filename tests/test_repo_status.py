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


class RepoStatusTest(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.readme = self.dir / "README.md"
        shutil.copy(ROOT / "profile" / "README.md", self.readme)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def run_at(self, now):
        repo_status.main(["--fixture", str(FIXTURE), "--readme", str(self.readme), "--now", now])
        return self.readme.read_text(encoding="utf-8")

    def test_table(self):
        text = self.run_at("2026-11-21T03:00:00+00:00")
        self.assertIn("リポジトリ **2** · 開いている PR **3** · CI が失敗 **1**", text)
        self.assertNotIn("secret-tool", text)  # 非公開は出さない
        self.assertIn("| [nok-pipe-in](https://github.com/nok-Inc-jp/nok-pipe-in)", text)
        self.assertIn("✅ 通過", text)
        self.assertIn("❌ 失敗", text)
        self.assertIn("`v0.4.0`", text)
        self.assertIn("プラグインを \\| 配る", text)  # 表を壊さない
        self.assertIn("アーカイブ済み：[old-thing]", text)
        self.assertNotIn("| [old-thing]", text)
        self.assertIn("### すぐ試す", text)  # 印の外は残す

    def test_time_only_change_is_not_written(self):
        first = self.run_at("2026-11-21T03:00:00+00:00")
        second = self.run_at("2026-11-21T09:00:00+00:00")
        self.assertEqual(first, second)

    def test_missing_markers(self):
        self.readme.write_text("no markers", encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.run_at("2026-11-21T03:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
