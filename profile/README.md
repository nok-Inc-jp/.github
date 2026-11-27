<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/nok-signature-h-reversed.svg">
    <img src="assets/nok-signature-h.svg" alt="NOK 株式会社ノック" width="320">
  </picture>
</p>

<h3 align="center">いい問いが、いいソフトウェアをつくる。</h3>

<p align="center">札幌のソフトウェア会社です。AIと組んで、Web・アプリ・組み込みまで、事業の課題を解いています。<br>GitHub では、AI と一緒に業務アプリを開発するための自社ツールを公開しています。</p>

<p align="center">
  <a href="https://nok-inc.jp">Webサイト</a> ·
  <a href="https://github.com/nok-Inc-jp/nok-pipe-in">nok-pipe-in</a> ·
  <a href="https://github.com/nok-Inc-jp/nok-plugins">nok-plugins</a> ·
  <a href="https://nok-inc.jp/recruit">採用情報</a> ·
  <a href="https://nok-inc.jp/contact">お問い合わせ</a>
</p>

## 開発フロー

業務アプリ開発の各工程を、Claude Code のプラグインで支えています。工程の区切りごとに人がレビューし、承認してから次の工程へ進みます。

<!-- flow:start -->
（初回の更新を待っています）
<!-- flow:end -->

## 開発状況

<!-- activity:start -->
（初回の更新を待っています）
<!-- activity:end -->

### 最近の更新

<!-- recent:start -->
（初回の更新を待っています）
<!-- recent:end -->

## クイックスタート

<details>
<summary>Claude Code にプラグインを追加する</summary>

```bash
/plugin marketplace add nok-Inc-jp/nok-plugins
/plugin install pipe-in@nok-plugins
```

</details>

<details>
<summary>pipe-in で開発パイプラインを動かす</summary>

```bash
git clone https://github.com/nok-Inc-jp/nok-pipe-in.git && cd nok-pipe-in
pip install -e .
pipe-in --project ~/src/myapp --text "ユーザー一覧に CSV 出力ボタンを追加したい"
```

</details>

<sub>このページの図と数字は、GitHub Actions が6時間ごとに更新しています（[実行履歴](https://github.com/nok-Inc-jp/.github/actions/workflows/update-profile.yml)）。</sub>
