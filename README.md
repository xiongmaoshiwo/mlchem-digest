# ML×Chem Daily Digest

機械学習×化学（LLM／GNN／MI）に関する新着論文を毎日収集し、日本語要約でメール配信します。

## 主な特徴
- 3〜4の主要ソースから取得（arXiv / Crossref / bioRxiv / Semantic Scholar*任意）
- ML系キーワード × 化学系キーワードの **AND** フィルタ
- OpenAI APIで **日本語3〜4文の要約**
- HTMLメールで配信（GmailなどのSMTP）
- GitHub Actionsで **毎朝9:00 JST** に自動実行

## セットアップ（超概要）
1. このリポジトリをGitHubに作成してファイルを配置
2. GitHub Secrets を設定
   - `OPENAI_API_KEY`
   - `SMTP_HOST`（例: smtp.gmail.com）
   - `SMTP_PORT`（例: 465）
   - `SMTP_USER`（送信元メールアドレス）
   - `SMTP_PASS`（アプリパスワード）
   - `RECIPIENT_EMAIL`（受信先）
   - （任意）`S2_API_KEY`（Semantic Scholar）
3. `config.yaml` のキーワードを必要に応じて編集
4. Actionsの「Run workflow」から手動実行 or 翌朝の自動実行を待つ
