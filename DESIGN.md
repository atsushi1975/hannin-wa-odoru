# ゲームプラットフォーム — 設計書

最終更新: 2026-10-28

第 1 弾「犯人は踊る」のルールは `RULES.md` を参照。ここでは「どう作るか」を定義する。

## 1. 全体構成

複数ゲームを載せられる共通基盤（`gameplatform/`）と、ゲーム固有の実装（`games/`）を分ける。

```
ブラウザ (HTML/CSS/JS + EventSource)
   │  fetch (JSON, X-Player-Token) / SSE
   ▼
web/            Flask 層（api.py, templates, static）
   │
   ├── gameplatform/  ゲーム非依存
   │   ├── rooms.py      ルーム・参加者・開始
   │   ├── sessions.py   ゲストトークン
   │   ├── games.py      ゲーム登録（GameDefinition）
   │   ├── realtime.py   SSE チケット・通知
   │   └── errors.py     共通例外
   │
   └── games/hannin/   ゲーム固有
       ├── __init__.py   登録・設定検証・操作ディスパッチ
       ├── cards.py
       ├── config.py
       ├── engine.py
       └── views.py
```

依存方向は一方向。

```
errors ← games ← rooms ← web
          ↑
     realtime, sessions

errors ← games/hannin（engine は Flask も rooms も知らない）
```

### なぜ分けるか

- **ゲーム追加が安い**: `GameDefinition` を登録するだけで、ルーム管理・認証・
  API・リアルタイム通知をそのまま使える。
- **ルールのテストが速い**: `engine.py` は HTTP なしで全ルールを検証できる。
- **情報漏洩の経路が 1 つ**: 公開処理は `GameDefinition.view`（犯人は `views.py`）だけ。
- **設定の分離**: ゲームごとの設定検証は `normalize_config` に閉じる。

## 2. データモデル

### 2.1 共通基盤

```python
Room
├── id: str                  # 8 桁
├── game_key: str            # "hannin" など
├── host_id: str
├── players: list[{id, name}]
├── config: dict             # 検証済みのゲーム設定
├── game: Any | None         # 開始後のゲーム実体
└── version: int             # ロビー変化の版番号

Session
├── token: str
├── room_id: str
├── player_id: str
└── issued_at: float

GameDefinition
├── key / title / description
├── min_players / max_players
├── create(players, config) -> game
├── view(game, player_id) -> dict     # 公開してよい情報だけ
├── act(game, player_id, payload)     # 操作の検証と適用
└── normalize_config(raw) -> dict     # 外部入力を検証
```

`GameDefinition` が要求するゲームの最小インターフェースは
`revision` / `phase` / `start()` の 3 つ（`GameLike`）。
それ以外の操作は `act` を通すので、ゲームごとに自由に設計できる。

### 2.2 犯人は踊る（ゲーム内部）

サーバーだけが保持する完全な状態。**クライアントにはそのまま渡さない。**

```python
Game
├── config: RuleConfig
├── players: list[Player]        # 席順（時計回り）
├── by_id: dict[str, Player]
├── phase: str                   # "lobby" | "turn" | "pending" | "finished"
├── revision: int                # 状態が変わるたびに +1
├── current_index: int
├── started_by: str | None
├── pending: Pending | None
├── played: list[PlayedEntry]    # 場に出たカード（公開）
├── events: list[Event]          # 公開ログ
└── result: Result | None

Player
├── id / name
├── hand: list[Card]             # 非公開
├── turns_taken: int
├── tricks_played: int
└── notes: list[Note]            # 本人だけ

Card
├── id: str                      # 例 "detective-2"（同一種でも一意）
└── type: CardType

Pending
├── kind: str                    # "trade" | "info_op"
├── actor_id / target_id
├── required: list[str]          # 選択が必要な人
└── selections: dict[str, str]   # player_id -> card_id（確定済み）
```

### カードの同一性

カードは **`Card.id` でのみ追跡する**。`type` で探して移動させると、
同種カードが複数あるときに「どの 1 枚が動いたか」が壊れる。
手札操作は必ず `card.id` をキーにした除去・追加で行う。

### 設定（`RuleConfig`）

`games/hannin/config.py` に集約。ルーム作成時に JSON で上書きできるのは
ゲーム挙動のスイッチだけ（人数はロビーの定員と一致させるため変更不可）。

```python
detective_min_turn: int = 2
detective_inert_with_only_detective_or_culprit: bool = True
culprit_wins_when_detectives_exhausted: bool = False
accomplice_catching_culprit_loses: bool = True
```

`normalize_config()` は未知のキー・型違い・範囲外をすべて 400 エラーにする。
黙って捨てると「設定したつもりで効いていない」事故になるため。

## 3. 状態機械

```
        start()
lobby ──────────► turn ◄──────────────┐
                    │                 │
                    │ 取り引き / 情報操作 │ 全員の選択が揃った
                    ▼                 │
                 pending ─────────────┘

turn / pending ──(勝利条件)──► finished
```

| 状態 | 意味 | 受け付ける操作 |
| --- | --- | --- |
| `lobby` | 開始前 | `start` |
| `turn` | 手番の人が 1 枚出す | `play_card`（手番の人のみ） |
| `pending` | 複数人の選択待ち | `select_card`（`required` の人だけ） |
| `finished` | 決着済み | なし |

- 不正な遷移は `InvalidAction`（= `gameplatform.errors.InvalidAction`、400）。
- `finished` に入ったら `pending` は必ず `None` に落とす。
- 「次は誰か」は `_advance_turn()` だけが決める。手札が空の席は飛ばし、
  全員空なら `stalemate` で終了する。

## 4. 効果の処理一覧

`play_card()` は「検証 → 手札から除去 → 記録 → 効果解決」の順で進む。

| カード | 追加入力 | 効果 |
| --- | --- | --- |
| 第一発見者 | — | 開始時のみ。手番は消費して次へ。 |
| 犯人 | — | 手札が最後の 1 枚のときだけ。犯人の勝ち。 |
| 探偵 | `targetPlayerId` | 対象が「犯人（アリバイなし）」なら勝ち。外れても手番は進む。 |
| いぬ | `targetPlayerId`, `targetCardIndex` | その位置の 1 枚を暴く。犯人なら勝ち、違えば元の位置に戻す。 |
| 少年 | — | 犯人カードの保持者を自分にだけ通知。 |
| 目撃者 | `targetPlayerId` | 対象の手札を自分にだけ通知（スナップショット）。 |
| 取り引き | `targetPlayerId` → 両者の `select_card` | 1 枚ずつ同時交換。 |
| 情報操作 | 手札を持つ全員の `select_card` | 全員が左隣へ 1 枚渡す。 |
| うわさ | — | 全員が右隣からランダムに 1 枚引く。 |
| たくらみ | — | `tricks_played += 1`。以後、犯人側の勝敗を共有。 |
| アリバイ / 一般人 | — | 効果なし。 |

### 入力検証の順序

1. `phase` と手番（`_require_turn`）
2. カードが手札にあるか（`_hand_card`）
3. 対象プレイヤーの妥当性（`_require_target`：存在・自分でない）
4. いぬの位置（`_pick_index`：範囲内）
5. カード固有の制約（`_validate_play`：犯人は最後の 1 枚、探偵は 2 周目から）

検証を通るまで状態は一切変更しない。

### 探偵の「不発」

手札が「探偵」と「犯人」だけのとき、2 周目前でも探偵を出せるが効果はない
（`detective_inert_with_only_detective_or_culprit`）。
判定は `_detective_is_inert()` に閉じ込め、対象選択もスキップする。

## 5. 同時解決アルゴリズム（シャドウコピー対策）

情報操作・うわさは「全員の手札が一斉に動く」ため、配列を走査しながら書き換えると
上書き・二重取得が起きる。そこで **スナップショット → 一括削除 → 一括追加** の 3 段階にする。

### 情報操作

```python
chosen = {player_id: Card}        # 1. 全員の選択を確定（読み取り専用）

for player_id, card in chosen.items():          # 2. 一括削除
    player.hand = [c for c in player.hand if c.id != card.id]

for player_id, card in chosen.items():          # 3. 一括追加
    destination = players[(index_of(player_id) + 1) % len(players)]
    destination.hand.append(card)
```

### うわさ

```python
snapshot = {player.id: list(player.hand) for player in players}   # 1. 固定
draws = [(source, drawer, picked_card), ...]                      # 2. 引く枚を決定
for source, _, card in draws: remove(card)                        # 3. 一括削除
for source, drawer, card in draws: append(drawer, card)           # 4. 一括追加
```

ポイント:

- 引くカードは **元の持ち主のスナップショット** から選ぶ。移動後の状態を参照しない。
- 削除は id 指定なので、同種カードが複数あっても取り違えない。
- 追加は削除が全部終わってから。途中で追加すると、後の削除がそれを消してしまう。

### 不変条件（テストで検証）

- カードの総数が増減しない（場に出た分を除く）。
- 同じ `card.id` が 2 人の手札に同時に存在しない。
- 全員の選択が揃うまで `pending` から抜けない。

## 6. 情報フィルタリング（チート対策）

公開処理は `GameDefinition.view`（犯人は `games/hannin/views.py` の `player_view`）だけ。
API は必ずこれを通し、ゲーム内部のオブジェクトを直接 `jsonify` しない。

| 情報 | 自分 | 他人 |
| --- | --- | --- |
| 手札の中身 | ○ | ×（`handCount` のみ） |
| private note（少年・目撃者・取り引き・うわさ） | ○ | × |
| 場に出たカード | ○ | ○ |
| 公開ログ | ○ | ○ |
| 選択待ちの `myOptions` | ○（必要時のみ） | ×（空配列） |
| 結果 | ○ | ○ |

返す JSON（概略）:

```json
{
  "revision": 7,
  "phase": "turn",
  "currentPlayerId": "p2",
  "startedBy": "p1",
  "players": [
    { "id": "p1", "name": "A", "handCount": 3, "turnsTaken": 1,
      "tricksPlayed": 0, "isCurrent": false, "isMe": true }
  ],
  "me": {
    "id": "p1",
    "hand": [ { "id": "...", "type": "...", "label": "...", "description": "..." } ],
    "notes": []
  },
  "played": [ { "playerId": "p1", "playerName": "A", "card": { "...": "..." } } ],
  "pending": null,
  "events": [ { "seq": 1, "kind": "start", "message": "..." } ],
  "result": null
}
```

`pending` の形:

```json
{
  "kind": "trade",
  "actorId": "p2",
  "targetId": "p3",
  "requiredPlayerIds": ["p2", "p3"],
  "waitingPlayerIds": ["p3"],
  "isRequired": true,
  "hasSelected": false,
  "myOptions": [ { "id": "...", "label": "..." } ]
}
```

`myOptions` は「選択が必要で、まだ選んでいない人」にだけ入れる。
テストでは他人のカード id が JSON 文字列に含まれないことまで確認している
（`tests/test_engine.py::ViewTest`）。

## 7. リアルタイム通知（SSE）

### 方式

WebSocket はこの環境（オフライン・追加パッケージ不可）では導入できないため、
**SSE（Server-Sent Events）** を使う。

- サーバー → クライアント: `GET /api/rooms/<id>/events` の 1 本のストリーム
- クライアント → サーバー: 通常の POST（操作時のみ）
- 通知内容は「更新あり」の合図だけ。本文は接続ごとに `view` で作り直す。

```
ブラウザ                     サーバー
  │ POST /stream-ticket        │  ヘッダのトークンで認証
  │ ─────────────────────────► │  60 秒有効・1 回限りのチケット発行
  │ ◄───────────────────────── │
  │ GET /events?ticket=...     │  チケットを消費
  │ ─────────────────────────► │
  │ ◄──── event: state ─────── │  接続直後に現在状態
  │                            │
  │        （誰かが操作）       │  publish(room_id)
  │ ◄──── event: state ─────── │  変更後の状態を都度生成
  │ ◄──── : ping（20 秒）───── │  無通信でも切れにくくする
```

### チケットを使う理由

`EventSource` は任意の HTTP ヘッダを付けられない。長命のトークンを
クエリ文字列に置くとアクセスログに残るため、次のように分ける。

1. 通常の API 呼び出し（ヘッダ認証）で短命チケットを発行
2. SSE 接続時だけチケットを渡す
3. チケットは 60 秒で失効し、1 回使うと無効

### 通知の粒度

`gameplatform/realtime.py` はルームごとに購読者を持つ。
`publish()` はキューに 1 つ積むだけで、内容は持たない。
そのため「誰に何を送るか」の判断が通知側に漏れない。

### フォールバック

SSE が切れたらクライアントは 3 秒後にチケットを取り直して再接続する。
加えて 5 秒間隔のポーリングを保険として残している。

### 将来 WebSocket にする場合

`gameplatform/realtime.py` の `issue_ticket` / `publish` / `subscribe` と、
`web/api.py` の `/events` を差し替える。ゲーム側は変更不要。

## 8. 楽観ロック（非同期ズレ対策）

複数クライアントが同じ状態を見ているため、古い画面から操作される可能性がある。

- ゲーム状態が変わるたびに `game.revision += 1`。
- ロビーの変化は `room.version += 1`。
- 操作には `expectedRevision` を付ける（省略も可）。
- 不一致なら **409** と最新の `game` ビューを返す。
- クライアントは 409 を受けたら、その `game` で画面を更新してやり直す。

すべての状態変更は `gameplatform.rooms.LOCK`（`threading.RLock`）の中で行う。
SSE の配信も同じロックでスナップショットを取ってから行う。

## 9. API

| メソッド | パス | 認証 | 内容 |
| --- | --- | --- | --- |
| GET | `/` | — | トップページ |
| GET | `/rooms/<room_id>` | — | ルームページ（参加フォーム兼用） |
| GET | `/api/games` | — | 登録済みゲームの一覧 |
| POST | `/api/rooms` | — | 作成。`{name, game?, config?}` |
| POST | `/api/rooms/<id>/join` | — | 参加。`{name}` |
| POST | `/api/rooms/<id>/leave` | ○ | 退出（開始前のみ） |
| POST | `/api/rooms/<id>/host` | ○ | ホスト権を引き継ぐ（開始前のみ） |
| GET | `/api/rooms/<id>` | — | 公開情報（参加者一覧） |
| GET | `/api/rooms/<id>/state` | ○ | 自分用にフィルタした状態 |
| POST | `/api/rooms/<id>/start` | ○ | ホストのみ。開始 |
| POST | `/api/rooms/<id>/actions` | ○ | ゲーム操作 |
| POST | `/api/rooms/<id>/stream-ticket` | ○ | SSE 用チケット |
| GET | `/api/rooms/<id>/events?ticket=` | チケット | SSE ストリーム |
| GET | `/api/health` | — | 死活確認 |

認証は `X-Player-Token` ヘッダ（または `Authorization: Bearer ...`）。
トークンはルームとプレイヤーに紐づき、他ルームでは使えない。

開始できるのはホストだけ。ただしブラウザの保存情報が消えるとホストが操作できず
ルームが詰むため、**開始前なら参加者の誰でも `/host` でホスト権を引き継げる**。
引き継ぎは `room.version` を進め、SSE で全員の画面に反映される。

`/actions` のボディ:

```json
{ "type": "play_card", "cardId": "civilian-0", "targetPlayerId": "p2",
  "targetCardIndex": 0, "expectedRevision": 12 }
```

```json
{ "type": "select_card", "cardId": "alibi-1", "expectedRevision": 13 }
```

`type` の解釈はゲーム側（`games/hannin/__init__.py` の `act`）が行う。
共通基盤は revision 検査と認証だけを担当する。

エラー:

| ステータス | 意味 |
| --- | --- |
| 400 | 入力不正・不正な操作（現在の `game` を添えて返す） |
| 403 | トークン不正・ホスト以外の開始・ゲーム中の退出 |
| 404 | ルーム／ゲームなし |
| 409 | `expectedRevision` が古い（最新の `game` を添えて返す） |

## 10. クライアント

`web/static/room.js` の状態:

```
session      : { token, playerId, name }   ※ localStorage
roomState    : ルーム公開情報
gameState    : view の内容
selectedCardId / targetPlayerId / targetCardIndex : 操作中の選択
busy         : 送信中フラグ（二重送信とポーリング停止に使う）
eventSource  : SSE 接続
```

描画は `apply(data)` → `renderGame()` の一方向。
サーバーが返した状態だけを描画し、クライアント側でゲーム状態を推測・保持しない。

## 11. テスト方針

| ファイル | 対象 |
| --- | --- |
| `tests/test_cards.py` | 32 枚・12 種類の定義 |
| `tests/test_config.py` | 人数別レシピの合計枚数、再現性 |
| `tests/test_engine.py` | 開始、犯人、探偵、いぬ、取り引き、同時解決、ビュー |
| `tests/test_gameplatform.py` | ゲーム登録、設定検証、退出・ホスト交代、セッション、SSE チケット |
| `tests/test_api.py` | 認証、ホスト権限、参加制限、revision、情報漏洩、退出、ゲーム一覧 |

テスト用に `Game(..., deck=[...])` でデッキを注入できる。
`tests/helpers.py` の `make_game()` はプレイヤー i にスライス `[i*4:(i+1)*4]` を配る。

重点的に確認していること:

- 同時解決後にカードが消えない・増えない・重複しない。
- 他人の手札 id が JSON に現れない。
- 古い revision の操作が 409 になる。
- アリバイ持ちは探偵に当てられても終了しない。
- 退出するとトークンが無効になり、ホストが交代する。
- 不正な設定キー・型・範囲が拒否される。

## 12. 既知の制限

- **メモリのみ**: サーバー再起動でルームとゲームは消える。
- **単一プロセス前提**: 複数ワーカーではルームも SSE 購読も共有されない。
  永続化・水平スケールには Redis Pub/Sub 等への置換が必要。
- **SSE は接続ごとにスレッドを 1 本使う**（開発サーバー）。人数分の接続を
  想定した本番構成では ASGI 等への移行が必要。
- **再接続はトークン頼み**: localStorage を消すと復帰できない。
- **観戦・チャット・タイマーは未実装**。再戦は同一ルームでは行わず、新しいルームを作る。
- **UI は素の JS**: React / Tailwind への移行は未実施（ネットワークが使える環境が整ったら検討）。

## 13. ゲームの追加手順

1. `games/<key>/` を作る（`cards.py` / `config.py` / `engine.py` / `views.py`）。
2. `GameDefinition` を作り、`normalize_config` / `create` / `view` / `act` を実装。
3. モジュール読み込み時に `gameplatform.games.register()` を呼ぶ。
4. `games/__init__.py` に `from . import <key>` を追加。
5. `tests/` にゲーム単体テストと API テストを追加。

ルーム管理・セッション・SSE・revision は共通基盤が面倒を見る。
