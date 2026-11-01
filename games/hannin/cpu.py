"""CPUプレイヤーの純ロジック（犯人推定と行動の判断）。

Web 層・engine の内部状態に依存させないこと。入力は ``player_view`` の返り値だけ。
"""

from __future__ import annotations

import logging
import random
from collections import deque
from collections.abc import Collection
from typing import Any

from .cards import CardType
from .config import HAND_SIZE, RuleConfig

logger = logging.getLogger("hannin.cpu")

# 探偵を出す確率のしきい値（固定。手札枚数などでは変えない）。
DETECTIVE_THRESHOLD = 0.72

# 推定が参照する直近の出札数（手数）。これより前の公開情報は使わない。None なら全期間。
DEFAULT_MEMORY_WINDOW = 10

_EPS = 1e-12
_CULPRIT = CardType.CULPRIT.value

# 手札が動く公開イベントと、そのとき自分に付く note の kind。
_MOVE_NOTES: dict[str, tuple[str, ...]] = {
    "rumor": ("rumor_draw",),
    "info_op": ("info_op_sent", "info_op_received"),
    "trade": ("trade",),
}
_RECEIVED_NOTE = {"rumor": "rumor_draw", "info_op": "info_op_received", "trade": "trade"}
_RESULT_NOTES = ("boy", "witness")


class CulpritEstimator:
    """p[i] = 「プレイヤー i が犯人カードを持っている確率」（合計は常に1）。

    ゲーム開始直後に作り、revision が変わるたびに ``update(view)`` を呼ぶ。
    1回の update に移動イベントが複数含まれる場合は seq 順に処理するが、
    自分の note の対応づけは kind ごとの順番で行う（1件ずつ届く前提が最も正確）。

    ``memory_window`` を指定すると、直近 N 手（出札数の増加）より前の情報は使わない。
    窓の開始点の view から推定をやり直す。None なら試合開始からの全情報を使う。
    """

    def __init__(self, my_id: str, memory_window: int | None = None) -> None:
        if memory_window is not None and memory_window < 0:
            raise ValueError("memory_window は 0 以上で指定してください")
        self.my_id = my_id
        self._memory_window = memory_window
        self._history: deque[dict[str, Any]] = deque()
        self._replaying = False
        self._ids: list[str] = []
        self._names: list[str] = []
        self._index: dict[str, int] = {}
        self._p: list[float] = []
        self._initialized = False
        self._holding = False
        self._hand_counts: list[int] = []
        self._seen_events: set[int] = set()
        self._seen_notes: set[int] = set()
        self._trick_ids: frozenset[str] = frozenset()

    # ------------------------------------------------------------------ 公開

    def probabilities(self) -> dict[str, float]:
        return dict(zip(self._ids, self._p))

    def trick_player_ids(self) -> frozenset[str]:
        """自分以外でたくらみを出した人（探偵の指名候補から外す）。"""
        return self._trick_ids

    def best_target(self, excluded_ids: Collection[str], rng: random.Random) -> str | None:
        return self._pick(excluded_ids, rng, largest=True)

    def worst_target(self, excluded_ids: Collection[str], rng: random.Random) -> str | None:
        return self._pick(excluded_ids, rng, largest=False)

    def update(self, view: dict[str, Any]) -> None:
        ids = [player["id"] for player in view["players"]]
        if not self._initialized:
            self._ids = ids
            self._names = [player.get("name", player["id"]) for player in view["players"]]
            self._index = {player_id: index for index, player_id in enumerate(ids)}
            if self.my_id not in self._index:
                raise ValueError("自分がプレイヤーに含まれていません")
        elif ids != self._ids:
            raise ValueError("プレイヤーの構成が変わりました")

        if self._memory_window is None:
            self._advance(view)
        else:
            self._remember(view)
            if self._forget_old_views():
                self._replay()
            else:
                self._advance(view)

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "p = %s",
                {name: round(value, 4) for name, value in zip(self._names, self._p)},
            )

    # ------------------------------------------------------------------ 窓

    def _remember(self, view: dict[str, Any]) -> None:
        """窓のために view を保存する。同じ revision なら最新の内容で置き換えて、履歴を増やさない。"""
        if self._history and self._history[-1]["revision"] == view["revision"]:
            self._history[-1] = view
        else:
            self._history.append(view)

    def _forget_old_views(self) -> bool:
        """出札数が 現在 − N 以上になる最も古い view より前を捨てる。開始点が動いたら True。"""
        threshold = len(self._history[-1]["played"]) - self._memory_window
        dropped = False
        while len(self._history[0]["played"]) < threshold:
            self._history.popleft()
            dropped = True
        return dropped

    def _replay(self) -> None:
        """窓の開始点の view で初期化し、以降の view を今までと同じ処理で流し直す。"""
        history = list(self._history)
        self._replaying = True
        try:
            self._start_window(history[0])
            for view in history[1:]:
                self._advance(view)
        finally:
            self._replaying = False

    def _start_window(self, view: dict[str, Any]) -> None:
        """窓の開始点。その時点の手札から初期化し、開始点に含まれる events / notes は適用しない。"""
        me = view["me"]
        holding = any(card["type"] == _CULPRIT for card in me["hand"])
        self._hand_counts = [int(player["handCount"]) for player in view["players"]]
        self._seen_events = {event["seq"] for event in view["events"]}
        self._seen_notes = {note["seq"] for note in me["notes"]}
        self._initialized = True
        self._holding = holding
        self._init_from_hand_counts(holding)
        self._settle(view, holding)
        self._update_trick_ids(view)

    # ------------------------------------------------------------------ 1 view の処理

    def _advance(self, view: dict[str, Any]) -> None:
        me = view["me"]
        now_holding = any(card["type"] == _CULPRIT for card in me["hand"])
        self._hand_counts = [int(player["handCount"]) for player in view["players"]]

        new_events = sorted(
            (event for event in view["events"] if event["seq"] not in self._seen_events),
            key=lambda event: event["seq"],
        )
        new_notes = sorted(
            (note for note in me["notes"] if note["seq"] not in self._seen_notes),
            key=lambda note: note["seq"],
        )
        self._seen_events.update(event["seq"] for event in new_events)
        self._seen_notes.update(note["seq"] for note in new_notes)

        if not self._initialized:
            self._init_probabilities(view.get("startedBy"), now_holding)
            self._initialized = True
            self._holding = now_holding

        queues: dict[str, deque[dict[str, Any]]] = {}
        for note in new_notes:
            queues.setdefault(note.get("kind", ""), deque()).append(note)

        moves = [event for event in new_events if event["kind"] in _MOVE_NOTES]
        holding = self._holding
        for position, event in enumerate(moves):
            after = now_holding if position == len(moves) - 1 else holding
            self._apply_move(event, queues, holding, after)
            holding = after

        results = sorted(
            (note for kind in _RESULT_NOTES for note in queues.get(kind, ())),
            key=lambda note: note["seq"],
        )
        for note in results:
            self._apply_result_note(note)

        self._settle(view, now_holding)
        self._holding = now_holding
        self._update_trick_ids(view)

    def _settle(self, view: dict[str, Any], now_holding: bool) -> None:
        revealed = next(
            (entry["playerId"] for entry in view.get("played", []) if entry["card"]["type"] == _CULPRIT),
            None,
        )
        if revealed in self._index:
            # 犯人カードが場に出た（ゲーム終了）。出した人で確定し、手札との照合はしない。
            self._set_certain(self._index[revealed])
        else:
            self._reconcile_with_hand(now_holding)
            self._zero_empty_hands()

    def _update_trick_ids(self, view: dict[str, Any]) -> None:
        self._trick_ids = frozenset(
            entry["playerId"]
            for entry in view.get("played", [])
            if entry["card"]["type"] == CardType.TRICK.value and entry["playerId"] != self.my_id
        )

    # ------------------------------------------------------------------ 初期値

    def _init_probabilities(self, finder_id: str | None, holding: bool) -> None:
        me = self._index[self.my_id]
        if holding:
            self._set_certain(me)
            return
        weights = [
            0 if index == me else (HAND_SIZE - 1 if player_id == finder_id else HAND_SIZE)
            for index, player_id in enumerate(self._ids)
        ]
        total = sum(weights)
        self._p = [weight / total for weight in weights]

    def _init_from_hand_counts(self, holding: bool) -> None:
        """窓の開始点の初期値。持っていれば確定、いなければ手札枚数に比例（手札0の人は0）。"""
        me = self._index[self.my_id]
        if holding:
            self._set_certain(me)
            return
        self._p = [0.0 if index == me else float(count) for index, count in enumerate(self._hand_counts)]
        self._normalize()

    # ------------------------------------------------------------------ 移動

    def _apply_move(
        self,
        event: dict[str, Any],
        queues: dict[str, deque[dict[str, Any]]],
        before: bool,
        after: bool,
    ) -> None:
        kind = event["kind"]
        notes = {note_kind: queues[note_kind].popleft() for note_kind in _MOVE_NOTES[kind] if queues.get(note_kind)}
        parsed = self._parse_move(event)
        if parsed is None:
            if not self._replaying:
                logger.warning("イベント seq=%s (%s) に有効な data が無いため、推定から読み飛ばします。", event.get("seq"), kind)
            return
        hand_counts, edges = parsed

        me = self._index[self.my_id]
        edges_in = [edge for edge in edges if edge[1] == me]
        edges_out = [edge for edge in edges if edge[0] == me]
        received = notes.get(_RECEIVED_NOTE[kind])
        received_culprit = received is not None and received.get("card", {}).get("type") == _CULPRIT

        if edges_in or edges_out:
            if received_culprit or (after and not before):
                self._set_certain(me)
                return
            if before:
                destination = next((dst for src, dst in edges_out if hand_counts[src] > 0), None)
                if after or destination is None:
                    self._set_certain(me)
                else:
                    self._set_certain(destination)
                return

        cancelled: set[tuple[int, int]] = set()
        if received is not None:
            sender = self._index.get(received.get("player_id"))
            if sender is not None:
                cancelled.add((sender, me))
            else:
                cancelled.update(edges_in)

        self._p = self._flow(hand_counts, edges, cancelled)
        self._normalize()

    def _parse_move(self, event: dict[str, Any]) -> tuple[list[int], list[tuple[int, int]]] | None:
        data = event.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("handCounts"), dict):
            return None
        counts = data["handCounts"]
        try:
            hand_counts = [int(counts[player_id]) for player_id in self._ids]
        except (KeyError, TypeError, ValueError):
            return None

        count = len(self._ids)
        if event["kind"] in ("rumor", "info_op"):
            return hand_counts, [(index, (index + 1) % count) for index in range(count)]
        actor = self._index.get(data.get("actorId"))
        target = self._index.get(data.get("targetId"))
        if actor is None or target is None:
            return None
        return hand_counts, [(actor, target), (target, actor)]

    def _flow(
        self,
        hand_counts: list[int],
        edges: list[tuple[int, int]],
        cancelled: set[tuple[int, int]],
    ) -> list[float]:
        """犯人カードを持つ人 src が、確率 1/h[src] で dst へ渡す（旧 p から同時に計算）。"""
        updated = list(self._p)
        for src, dst in edges:
            if (src, dst) in cancelled or hand_counts[src] <= 0:
                continue
            moved = self._p[src] / hand_counts[src]
            updated[src] -= moved
            updated[dst] += moved
        return updated

    # ------------------------------------------------------- 自分の手番の結果

    def _apply_result_note(self, note: dict[str, Any]) -> None:
        target = self._index.get(note.get("player_id"))
        if target is None:
            return
        if note["kind"] == "boy":
            self._set_certain(target)
        elif any(card.get("type") == _CULPRIT for card in note.get("cards", [])):
            self._set_certain(target)
        else:
            self._p[target] = 0.0
            self._normalize()

    # ------------------------------------------------------------ 正規化など

    def _reconcile_with_hand(self, holding: bool) -> None:
        """自分の手札と矛盾しないようにする（持っていれば1、持っていなければ0）。"""
        me = self._index[self.my_id]
        if holding:
            self._set_certain(me)
        elif self._p[me] != 0.0:
            self._p[me] = 0.0
            self._normalize()

    def _zero_empty_hands(self) -> None:
        """手札が0枚の人は犯人カードを持てない。"""
        if any(value != 0.0 and count == 0 for value, count in zip(self._p, self._hand_counts)):
            self._p = [0.0 if count == 0 else value for value, count in zip(self._p, self._hand_counts)]
            self._normalize()

    def _set_certain(self, index: int) -> None:
        self._p = [1.0 if i == index else 0.0 for i in range(len(self._ids))]

    def _normalize(self) -> None:
        total = sum(self._p)
        if total > _EPS:
            self._p = [value / total for value in self._p]
            return
        me = self._index[self.my_id]
        others = [i for i in range(len(self._ids)) if i != me]
        holders = [i for i in others if self._hand_counts[i] > 0] or others
        if not self._replaying:
            logger.warning("確率の合計が0になりました。手札のある他プレイヤーで均等に戻します。")
        self._p = [1 / len(holders) if i in holders else 0.0 for i in range(len(self._ids))]

    def _pick(self, excluded_ids: Collection[str], rng: random.Random, *, largest: bool) -> str | None:
        candidates = [
            (player_id, value)
            for player_id, value in zip(self._ids, self._p)
            if player_id not in excluded_ids
        ]
        if not candidates:
            return None
        best = (max if largest else min)(value for _, value in candidates)
        tied = [player_id for player_id, value in candidates if abs(value - best) <= _EPS]
        return tied[0] if len(tied) == 1 else rng.choice(tied)


# ---------------------------------------------------------------------- 行動の判断


def choose_action(
    view: dict[str, Any],
    estimator: CulpritEstimator,
    rng: random.Random,
    config: RuleConfig = RuleConfig(),
) -> dict[str, Any] | None:
    """公開ビューだけから行動を決める。返す payload は ``act()`` が受け取る形。

    行動すべき場面でなければ None。``estimator`` は事前に ``update(view)`` 済みであること。
    """
    me_id = view["me"]["id"]
    if view["phase"] == "pending":
        return _choose_selection(view, rng)
    if view["phase"] != "turn" or view.get("currentPlayerId") != me_id:
        return None
    return _choose_play(view, estimator, rng, config)


def _choose_selection(view: dict[str, Any], rng: random.Random) -> dict[str, Any] | None:
    pending = view.get("pending")
    if not pending or not pending["isRequired"] or pending["hasSelected"] or not pending["myOptions"]:
        return None
    return {"type": "select_card", "cardId": rng.choice(pending["myOptions"])["id"]}


def _choose_play(
    view: dict[str, Any],
    estimator: CulpritEstimator,
    rng: random.Random,
    config: RuleConfig,
) -> dict[str, Any] | None:
    me_id = view["me"]["id"]
    hand = view["me"]["hand"]
    turns_taken = next(player["turnsTaken"] for player in view["players"] if player["id"] == me_id)
    others = [player for player in view["players"] if player["id"] != me_id]
    other_ids = [player["id"] for player in others]
    with_hand = [player["id"] for player in others if player["handCount"] > 0]
    played_trick = any(
        entry["playerId"] == me_id and entry["card"]["type"] == CardType.TRICK.value for entry in view["played"]
    )

    def is_inert(card: dict[str, Any]) -> bool:
        if not config.detective_inert_with_only_detective_or_culprit:
            return False
        rest = [c for c in hand if c["id"] != card["id"]]
        allowed = (CardType.DETECTIVE.value, _CULPRIT)
        return any(c["type"] == _CULPRIT for c in rest) and all(c["type"] in allowed for c in rest)

    def playable(card: dict[str, Any]) -> bool:
        match card["type"]:
            case CardType.CULPRIT.value:
                return len(hand) == 1
            case CardType.FIRST_FINDER.value:
                return False
            case CardType.DETECTIVE.value:
                return is_inert(card) or turns_taken >= config.detective_min_turn - 1
            case CardType.DOG.value:
                return bool(with_hand)
            case _:
                return True

    def play(card: dict[str, Any], target: str | None = None, **extra: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {"type": "play_card", "cardId": card["id"]}
        if target is not None:
            payload["targetPlayerId"] = target
        payload.update(extra)
        return payload

    def random_target() -> str:
        return rng.choice(with_hand or other_ids)

    cards = [card for card in hand if playable(card)]
    detectives = [card for card in cards if card["type"] == CardType.DETECTIVE.value]
    rest = [card for card in cards if card["type"] != CardType.DETECTIVE.value]

    if detectives:
        detective = detectives[0]
        if is_inert(detective):
            return play(detective)
        no_hand = {pid for pid in other_ids if pid not in with_hand}
        if played_trick:
            if not rest:
                # たくらみ済みでは、犯人に当たると自分が負けるので p が最小の人を指名する。
                strict = {me_id} | no_hand
                target = estimator.worst_target(strict, rng) or estimator.worst_target({me_id}, rng)
                return play(detective, target or random_target())
        else:
            excluded = {me_id} | no_hand | set(estimator.trick_player_ids())
            target = estimator.best_target(excluded, rng)
            if target is not None and estimator.probabilities()[target] >= DETECTIVE_THRESHOLD:
                return play(detective, target)
            if not rest:
                target = (
                    target
                    or estimator.best_target({me_id} | no_hand, rng)
                    or estimator.best_target({me_id}, rng)
                )
                return play(detective, target or random_target())

    if not rest:
        return None
    card = rng.choice(rest)
    match card["type"]:
        case CardType.WITNESS.value | CardType.TRADE.value:
            return play(card, random_target())
        case CardType.DOG.value:
            if played_trick:
                target = estimator.worst_target({me_id} | {pid for pid in other_ids if pid not in with_hand}, rng)
            else:
                target = None
            return play(card, target or rng.choice(with_hand), targetCardIndex=None)
        case _:
            return play(card)
