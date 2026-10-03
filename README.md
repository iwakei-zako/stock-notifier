# 株スクリーニング → LINE 通知

東証（プライム・スタンダード・グロース）の全銘柄から、`config.yaml` の条件に合う銘柄を探します。
結果はスマホで見やすい Web ページ（GitHub Pages）にまとめ、毎朝 7:30（平日）に LINE へ「短い要約＋ページの URL」を送ります。
前営業日が祝日だった朝は送りません。

- 株価データ: Yahoo Finance（yfinance）。前営業日の終値で判定します
- 銘柄一覧: JPX（日本取引所グループ）の上場銘柄一覧
- ニュース: TDnet の適時開示（前営業日 15:00 以降の業績修正・増配・TOB など）
- マーケット概況: 日経平均・日経先物・米国株・SOX・VIX・ドル円・米10年金利
- 実行環境: GitHub Actions（PC の電源が切れていても動きます）
- 結果ページ: GitHub Pages（日付ごとのページが残るので、過去の結果も見返せます）
- 通知: LINE Messaging API（無料プランは月200通まで。1日1通なら十分です）

## ファイル構成

| ファイル | 内容 |
|---|---|
| `main.py` | 本体（銘柄の取得 → 判定 → レポート作成 → LINE 送信） |
| `report.py` | 結果ページ（HTML）のデザイン |
| `config.yaml` | 条件の ON/OFF と数値。**ここを編集して条件を調整します** |
| `.github/workflows/daily.yml` | 毎朝自動で実行するための設定 |
| `docs/` | 作成されたレポート（GitHub Actions が毎朝自動で保存します。手で触る必要はありません） |

## セットアップ手順

### 1. LINE 側の準備

1. [LINE Official Account Manager](https://manager.line.biz/) で LINE 公式アカウントを作ります（自分専用なので、名前は「株通知」などで OK）。
2. 作ったアカウントの「設定」→「Messaging API」→「Messaging API を利用する」を押し、プロバイダーを作成または選択します。
3. [LINE Developers コンソール](https://developers.line.biz/console/) を開き、作ったチャネルを選びます。
   - 「Messaging API設定」タブ → 一番下の **チャネルアクセストークン（長期）** を「発行」してコピーします。
   - 「チャネル基本設定」タブ → 一番下の **あなたのユーザーID**（`U` で始まる文字列）をコピーします。
   - 「Messaging API設定」タブの QR コードを LINE アプリで読み取り、公式アカウントを **友だち追加** します。
4. （任意）Official Account Manager の「応答設定」で「応答メッセージ」をオフにすると、自動返信が来なくなります。

### 2. PC で動作確認（任意）

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt

# レポートを docs/ に作り、LINE に送らず要約を画面に表示（全銘柄で 2 分ほど）
# 表示された docs\reports\〜.html をダブルクリックするとブラウザで見られます
.\.venv\Scripts\python main.py --dry-run
```

### 3. GitHub に置いて毎朝自動実行する

1. [GitHub](https://github.com/) で新しいリポジトリを作ります。
2. 「uploading an existing file」から `main.py` / `report.py` / `config.yaml` / `requirements.txt` / `README.md` / `.gitignore` をアップロードします（`.venv` と `docs` フォルダは不要です）。
3. 「Add file」→「Create new file」で、ファイル名に `.github/workflows/daily.yml` と入力し、手元の同じファイルの中身を貼り付けて保存します。
4. リポジトリの「Settings」→「Secrets and variables」→「Actions」→「New repository secret」で、次の2つを登録します（Name に左側、Secret に値）。
   - `LINE_CHANNEL_ACCESS_TOKEN`: チャネルアクセストークン
   - `LINE_USER_ID`: あなたのユーザーID
5. リポジトリを公開にします（無料プランの GitHub Pages は公開リポジトリのみ）。
   - 先に、GitHub 右上のアイコン →「Settings」→「Emails」で **「Keep my email addresses private」** にチェックが入っていることを確認します。
   - リポジトリの「Settings」→「General」→ 一番下の「Danger Zone」→「Change visibility」→「Change to public」。
6. リポジトリの「Settings」→「Pages」→「Build and deployment」の **Source** を **「GitHub Actions」** にします。
7. 「Actions」タブ →「毎朝の株スクリーニング」→「Run workflow」で手動実行し、LINE に届くか確認します。

この後は、平日の朝 7:30 ごろに自動で届きます。GitHub の混雑状況によって、数分〜数十分遅れることがあります。
結果ページの URL は `https://<GitHubのユーザー名>.github.io/<リポジトリ名>/` です（最新の結果がいつでも見られます）。

## 条件を変えたいとき

`config.yaml` を編集します（GitHub 上なら、ファイルを開いて鉛筆アイコンから直接編集できます）。

- `enabled: true / false` … その条件を使うかどうか
- `threshold` や `ratio` … 条件の厳しさ
- `universe.min_turnover` … 売買代金が少ない銘柄を除外する基準（初期値 1 億円）
- `notify.max_per_condition` … 1つの条件につき最初に表示する件数（残りは「残りを表示」で開けます）

新しい種類の条件を足したいときは、`main.py` に `check_〇〇` 関数を追加して `CHECKS` に登録し、`config.yaml` に同じ名前の設定を書きます。

## 注意点

- 結果ページとリポジトリは公開されます。含まれるのはコード・設定・株の結果だけで、LINE のトークンやユーザーID（Secrets）は公開されません。
- Yahoo Finance は非公式に利用しているデータ源です。アクセス制限やデータの遅れ・誤りが起きることがあります。
  - 前営業日の終値がまだ反映されていない場合は、「⚠️ 未反映」と表示されます。
  - 株式分割や併合が反映されていない（株価に不自然な段差がある）銘柄は、自動で除外しています。
- エラーで止まった場合は、LINE にエラー通知（ログのページへのリンク付き）が届きます。
- このシステムは条件に合う銘柄を機械的に抽出するだけです。売買の判断はご自身で行ってください。
