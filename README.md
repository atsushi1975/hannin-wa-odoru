# ゲームプラットフォーム（第1弾: 犯人は踊る）

複数のカードゲームを載せられる、オンライン対戦用の共通基盤。
第 1 弾として「犯人は踊る」を実装している。

- ルール: [RULES.md](RULES.md)
- 設計（スキーマ・状態機械・API）: [DESIGN.md](DESIGN.md)
- リポジトリ: https://github.com/atsushi1975/hannin-wa-odoru

## いまの状態

| フェーズ | 内容 | 状態 |
| --- | --- | --- |
| 1 | 共通基盤（ルーム・ゲストセッション・設定・リアルタイム通知） | 実装済み |
| 2 | 犯人は踊るのコアロジック（12 種のカード・勝敗判定） | 実装済み |
| 3 | UI/UX（カード演出・目を閉じる演出など） | 基本 UI のみ |

- 実装済み: ルーム作成/参加/退出、ゲストトークン、ゲーム登録、
  ルームごとの設定、開始、ホスト権の引き継ぎ（開始前）、全カード効果、同時解決、勝敗判定、
  プレイヤー別の情報フィルタ、revision による楽観ロック、SSE による即時更新
- 未実装: 観戦、切断復帰の補助、永続化、手番タイマー、チャット、
  カード移動アニメーション、React への移行
- 仕様: 決着後の再戦は行わない（新しいルームを作る）。

## 必要なもの

- Python 3.11 以上（開発は 3.13 で確認）
- Flask 3.x

```powershell
pip install -r requirements.txt
```

すでに Flask が入っている環境なら追加インストールは不要。

## 起動

### 開発用（Flask 開発サーバー）

```bash
cd hannin-wa-odoru
python app.py
```

既定では `0.0.0.0:5000` で待ち受けます。`http://127.0.0.1:5000`（または `http://<PCのIPアドレス>:5000`）を開いてください。
ポートを変える場合は `--port` を指定します（`python app.py --port 5055`）。自動リロードは `--debug` を付けます。

開発サーバーを LAN に公開するため、信頼できるネットワークでのみ使うこと。

### 本番用（gunicorn、Linux / Docker / VPS）

```bash
cd hannin-wa-odoru
pip install -r requirements.txt
SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')" \
PORT=5000 gunicorn -w 1 -k gthread --threads 64 -b 0.0.0.0:${PORT:-5000} "web:create_app()"
```

- **ワーカーは `-w 1` のまま変えないこと。** ルーム・ゲーム状態・セッションはプロセスのメモリ上にあり、複数ワーカーにするとルームが共有されず壊れる。並行処理は `--threads` で行う。
- `--threads` は SSE（リアルタイム更新）の接続数の上限になる。接続 1 本がスレッド 1 本を使う。
- `SECRET_KEY` は必ず環境変数で渡す。未設定の場合は警告が出て、起動ごとのランダムな値で動く。
- `PORT` は待ち受けポート（既定 5000）。`game-lobby/servers.json` の `health` / `url` のポートと合わせる。
- WebSocket は使っていないため、gunicorn の worker class は標準の `gthread` でよい（eventlet / gevent は不要）。
- gunicorn は Windows では動かない。Windows で動かす場合は開発用の `python app.py` を使う。

## 遊び方

1. 1 人がトップページで名前を入れて「ルームを作成」。
2. 表示されたルーム ID を他の人に共有する。
3. 他の人はトップページの「ルームに参加」で ID と名前を入力。
4. 3 人以上集まったらホストが「ゲーム開始」。
5. 手番の人は手札から 1 枚選び、必要なら対象を選んで「出す」。
6. 取り引き・情報操作では、指示に従って自分のカードを 1 枚選ぶ。
7. 誰かが勝利条件を満たすと結果が表示される。

同じブラウザで複数人を試す場合は、通常ウィンドウとシークレットウィンドウを併用する
（セッションは `localStorage` に保存されるため）。

## テスト

```powershell
cd hannin-wa-odoru
python -m unittest discover -s tests -t . -q
```

個別に実行:

```powershell
python -m unittest tests.test_engine.DetectiveTest.test_alibi_blocks_detective -v
```

現在 58 件。ゲームロジック（同時解決の整合性）、情報漏洩、API の認証・revision、
ルーム退出とホスト交代、ゲーム登録、設定検証、SSE チケットを確認している。

## 構成

```
hannin-wa-odoru/
├── app.py                開発サーバーの起動
├── requirements.txt
├── RULES.md              ルール仕様・確定した解釈
├── DESIGN.md             設計書
├── gameplatform/         ゲーム非依存の共通基盤
│   ├── errors.py         共通例外
│   ├── sessions.py       ゲストセッション（トークン）
│   ├── games.py          ゲーム登録（GameDefinition）
│   ├── rooms.py          ルーム管理
│   └── realtime.py       SSE のチケットと通知
├── games/
│   └── hannin/           第1弾「犯人は踊る」
│       ├── __init__.py   共通基盤への登録・設定検証
│       ├── cards.py
│       ├── config.py
│       ├── engine.py
│       └── views.py
├── web/                  Flask 層
│   ├── __init__.py       アプリファクトリ
│   ├── api.py            JSON API / SSE
│   ├── templates/
│   └── static/
└── tests/
```

ゲームを追加するときは `games/<key>/` を作り、`GameDefinition` を登録して
`games/__init__.py` に 1 行足す。ルーム管理・API・リアルタイム通知は再利用できる。

## 設計上の注意

### リアルタイム通信について

当初案は WebSocket（Socket.io）だが、依存を増やさない方針のため、
追加パッケージ不要の **SSE（Server-Sent Events）** で実装している。

- サーバー → クライアントの即時通知（再接続は自動）
- クライアント → サーバーは通常の POST（操作時のみ）
- 認証はヘッダで行い、EventSource には 1 回限りの短命チケットを渡す
- 通知は「更新あり」の合図だけで、内容は接続ごとに `views` で作り直す

リアルタイム層は `gameplatform/realtime.py` に閉じている。将来 Socket.io /
WebSocket を導入する場合も、このモジュールと `web/api.py` の `/events` を
差し替えればよい。

### フロントエンドについて

暫定案は React + Tailwind だが、依存を増やさない方針のため現状は素の HTML/CSS/JS。
API は画面に依存しないので、後から React に置き換えられる。

### 開発用の制限

- 状態は **メモリのみ**。サーバーを再起動するとルームは消える。
- **単一プロセス専用**。複数ワーカーで起動するとルームが共有されない（`gunicorn -w 1` を維持）。
- `app.py` は Flask 標準の開発サーバー。そのままインターネットに公開しない（公開時は gunicorn を使う）。
- `SECRET_KEY` は環境変数で渡す。未設定だと警告が出る。

## 次にやること

- カード移動のアニメーション、山札・捨て札の表現
- 永続化（Redis 等）と複数プロセス対応
- 手番タイマー、切断時の扱い
- 2 本目以降のゲーム（UNO / 大富豪 / ノイ / ニムト）
- Node.js + React + Socket.io 構成への移行を検討（ネットワークは利用可能。依存追加の判断が必要）
