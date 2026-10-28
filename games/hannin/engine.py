"""犯人は踊るのゲームエンジン。

Web 層（Flask）に依存させないこと。すべての状態遷移と判定をここで完結させる。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from gameplatform.errors import InvalidAction

from .cards import Card, CardType
from .config import HAND_SIZE, RuleConfig, build_deck

LOBBY = "lobby"
TURN = "turn"
PENDING = "pending"
FINISHED = "finished"

__all__ = [
    "FINISHED",
    "LOBBY",
    "PENDING",
    "TURN",
    "Event",
    "Game",
    "InvalidAction",
    "Pending",
    "Player",
]


@dataclass(slots=True)
class Player:
    id: str
    name: str
    hand: list[Card] = field(default_factory=list)
    turns_taken: int = 0
    tricks_played: int = 0
    notes: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class Event:
    seq: int
    kind: str
    message: str


@dataclass(slots=True)
class Pending:
    """全員または複数人の選択が揃うまで解決しない効果。"""

    kind: str  # "info_op" | "trade"
    actor_id: str
    target_id: str | None
    required: list[str]
    selections: dict[str, str] = field(default_factory=dict)

    def is_complete(self) -> bool:
        return all(player_id in self.selections for player_id in self.required)


class Game:
    def __init__(
        self,
        players: list[tuple[str, str]],
        *,
        config: RuleConfig | None = None,
        rng: random.Random | None = None,
        deck: list[Card] | None = None,
    ) -> None:
        self.config = config or RuleConfig()
        if not (self.config.min_players <= len(players) <= self.config.max_players):
            raise ValueError(
                f"人数は {self.config.min_players}〜{self.config.max_players} 人です: {len(players)}"
            )
        ids = [player_id for player_id, _ in players]
        if len(set(ids)) != len(ids):
            raise ValueError("プレイヤー id が重複しています")

        self.rng = rng or random.Random()
        self.players = [Player(player_id, name) for player_id, name in players]
        self.by_id = {player.id: player for player in self.players}
        self.phase = LOBBY
        self.revision = 0
        self.current_index = 0
        self.started_by: str | None = None
        self.pending: Pending | None = None
        self.played: list[dict[str, Any]] = []
        self.events: list[Event] = []
        self.result: dict[str, Any] | None = None
        self._deck = list(deck) if deck is not None else None
        if self._deck is not None:
            expected = len(players) * HAND_SIZE
            if len(self._deck) != expected:
                raise ValueError(f"テスト用デッキの枚数が不正です: {len(self._deck)} != {expected}")
            if len({card.id for card in self._deck}) != len(self._deck):
                raise ValueError("テスト用デッキに同じ id のカードがあります")

    # ------------------------------------------------------------------ 開始

    def start(self) -> None:
        if self.phase != LOBBY:
            raise InvalidAction("ゲームはすでに開始しています。")

        deck = self._deck if self._deck is not None else build_deck(len(self.players), self.rng)
        for index, player in enumerate(self.players):
            player.hand = deck[index * HAND_SIZE : (index + 1) * HAND_SIZE]

        finder = next(
            (player for player in self.players if any(card.type is CardType.FIRST_FINDER for card in player.hand)),
            None,
        )
        if finder is None:
            raise ValueError("山札に第一発見者がありません")

        first_finder = next(card for card in finder.hand if card.type is CardType.FIRST_FINDER)
        finder.hand.remove(first_finder)
        self._played(finder, first_finder)
        self.started_by = finder.id
        self.current_index = self._index_of(finder)
        self._log("start", f"{finder.name} が第一発見者として事件を宣言した。")
        self.phase = TURN
        self.revision += 1
        self._advance_turn()

    # ------------------------------------------------------------ プレイヤー操作

    def play_card(
        self,
        player_id: str,
        card_id: str,
        *,
        target_player_id: str | None = None,
        target_card_index: int | None = None,
    ) -> None:
        self._require_turn(player_id)
        player = self.by_id[player_id]
        card = self._hand_card(player, card_id)
        inert = card.type is CardType.DETECTIVE and self._detective_is_inert(player, card)

        target: Player | None = None
        target_index: int | None = None
        if card.type in (CardType.DETECTIVE, CardType.DOG, CardType.WITNESS, CardType.TRADE) and not inert:
            target = self._require_target(player, target_player_id)
            if card.type is CardType.DOG:
                if not target.hand:
                    raise InvalidAction("対象の手札がありません。")
                target_index = self._pick_index(target, target_card_index)

        self._validate_play(player, card, inert)

        player.hand.remove(card)
        player.turns_taken += 1
        self._played(player, card)
        self.revision += 1
        self._log("play", f"{player.name} が「{card.label}」を出した。")
        self._resolve_play(player, card, target, target_index, inert)

    def submit_selection(self, player_id: str, card_id: str) -> None:
        if self.phase != PENDING or self.pending is None:
            raise InvalidAction("いまはカードを選ぶ場面ではありません。")
        pending = self.pending
        if player_id not in pending.required:
            raise InvalidAction("あなたは選択する必要がありません。")
        if player_id in pending.selections:
            raise InvalidAction("すでにカードを選んでいます。")
        player = self.by_id[player_id]
        card = self._hand_card(player, card_id)
        pending.selections[player_id] = card.id
        self.revision += 1
        if pending.is_complete():
            self._resolve_pending()

    # ---------------------------------------------------------------- 判定補助

    def is_culprit(self, player: Player) -> bool:
        """犯人カードを持ち、アリバイを持たない人。"""
        has_culprit = any(card.type is CardType.CULPRIT for card in player.hand)
        has_alibi = any(card.type is CardType.ALIBI for card in player.hand)
        return has_culprit and not has_alibi

    def culprit_holder(self) -> Player | None:
        for player in self.players:
            if any(card.type is CardType.CULPRIT for card in player.hand):
                return player
        return None

    def accomplices(self) -> list[Player]:
        return [player for player in self.players if player.tricks_played > 0]

    # ------------------------------------------------------------------ 内部処理

    def _resolve_play(
        self,
        player: Player,
        card: Card,
        target: Player | None,
        target_index: int | None,
        inert: bool,
    ) -> None:
        match card.type:
            case CardType.CULPRIT:
                self._finish_culprit_play(player)
            case CardType.DETECTIVE:
                if inert:
                    self._log("detective_inert", "手札が探偵と犯人だけのため、探偵の効果はなかった。")
                    self._after_detective()
                elif target is not None and self.is_culprit(target):
                    self._finish_catch(player, target, "detective")
                else:
                    self._log("detective_miss", f"{player.name} の指名は外れた。")
                    self._after_detective()
            case CardType.DOG:
                self._resolve_dog(player, target, target_index)
            case CardType.BOY:
                holder = self.culprit_holder()
                if holder is None:
                    self._note(player, "boy", "犯人の居場所は分からなかった。")
                else:
                    self._note(player, "boy", f"犯人カードを持っているのは {holder.name}。", player_id=holder.id)
                self._log("boy", f"{player.name} が少年を使い、犯人の居場所を確認した。")
                self._after_action()
            case CardType.WITNESS:
                assert target is not None
                self._note(
                    player,
                    "witness",
                    f"{target.name} の手札を見た。",
                    player_id=target.id,
                    cards=[c.to_dict() for c in target.hand],
                )
                self._log("witness", f"{player.name} が {target.name} の手札を見た。")
                self._after_action()
            case CardType.TRADE:
                assert target is not None
                if player.hand and target.hand:
                    self.pending = Pending(
                        kind="trade",
                        actor_id=player.id,
                        target_id=target.id,
                        required=[player.id, target.id],
                    )
                    self.phase = PENDING
                    self._log("trade_start", f"{player.name} が {target.name} と取り引きを始めた。")
                else:
                    self._log("trade_skip", "手札がないため、取り引きは行われなかった。")
                    self._after_action()
            case CardType.INFO_OP:
                required = [p.id for p in self.players if p.hand]
                if required:
                    self.pending = Pending(kind="info_op", actor_id=player.id, target_id=None, required=required)
                    self.phase = PENDING
                    self._log("info_op_start", "情報操作: 全員が左隣へ渡すカードを選ぶ。")
                else:
                    self._log("info_op_skip", "渡せる手札がないため、情報操作は何も起こらなかった。")
                    self._after_action()
            case CardType.RUMOR:
                self._resolve_rumor()
                self._after_action()
            case CardType.TRICK:
                player.tricks_played += 1
                self._log("trick", f"{player.name} は犯人の味方になった。")
                self._after_action()
            case CardType.ALIBI | CardType.CIVILIAN:
                self._after_action()
            case CardType.FIRST_FINDER:
                raise InvalidAction("第一発見者はゲーム開始時だけ使います。")

    def _resolve_dog(self, player: Player, target: Player | None, index: int | None) -> None:
        assert target is not None and index is not None
        revealed = target.hand.pop(index)
        self._log("dog_reveal", f"{player.name} のいぬが {target.name} の「{revealed.label}」を暴いた。")
        if revealed.type is CardType.CULPRIT:
            self._played(target, revealed)
            self._finish_catch(player, target, "dog")
        else:
            target.hand.insert(index, revealed)
            self._after_action()

    def _resolve_rumor(self) -> None:
        # 同時解決: 先に全員分の「引くカード」をスナップショットから決め、
        # その後で一括削除 -> 一括追加する（上書き事故を防ぐ）。
        snapshot = {player.id: list(player.hand) for player in self.players}
        draws: list[tuple[Player, Player, Card]] = []
        for index, player in enumerate(self.players):
            source = self.players[(index - 1) % len(self.players)]  # 右隣
            candidates = snapshot[source.id]
            if candidates:
                drawn = candidates[self.rng.randrange(len(candidates))]
                draws.append((source, player, drawn))

        for source, _, card in draws:
            source.hand = [c for c in source.hand if c.id != card.id]
        for source, drawer, card in draws:
            drawer.hand.append(card)
            self._note(
                drawer,
                "rumor_draw",
                f"うわさで {source.name} から「{card.label}」を引いた。",
                player_id=source.id,
                card=card.to_dict(),
            )
        self._log("rumor", "うわさ: 全員が右隣から1枚引いた。")

    def _resolve_pending(self) -> None:
        pending = self.pending
        assert pending is not None
        chosen: dict[str, Card] = {}
        for player_id, card_id in pending.selections.items():
            player = self.by_id[player_id]
            chosen[player_id] = self._hand_card(player, card_id)

        if pending.kind == "info_op":
            # 同時解決: 全員の選択を確定してから、一括削除 -> 一括追加。
            for player_id, card in chosen.items():
                player = self.by_id[player_id]
                player.hand = [c for c in player.hand if c.id != card.id]
            for player_id, card in chosen.items():
                source = self.by_id[player_id]
                destination = self.players[(self._index_of(source) + 1) % len(self.players)]
                destination.hand.append(card)
                self._note(
                    source,
                    "info_op_sent",
                    f"情報操作で {destination.name} に「{card.label}」を渡した。",
                    player_id=destination.id,
                    card=card.to_dict(),
                )
                self._note(
                    destination,
                    "info_op_received",
                    f"情報操作で {source.name} から「{card.label}」を受け取った。",
                    player_id=source.id,
                    card=card.to_dict(),
                )
            self._log("info_op", "情報操作: 全員が左隣に1枚渡した。")
        elif pending.kind == "trade":
            assert pending.target_id is not None
            actor = self.by_id[pending.actor_id]
            target = self.by_id[pending.target_id]
            actor_card = chosen[actor.id]
            target_card = chosen[target.id]
            actor.hand = [c for c in actor.hand if c.id != actor_card.id]
            target.hand = [c for c in target.hand if c.id != target_card.id]
            actor.hand.append(target_card)
            target.hand.append(actor_card)
            self._note(
                actor,
                "trade",
                f"{target.name} と取り引きし、「{actor_card.label}」を渡して「{target_card.label}」を受け取った。",
                player_id=target.id,
                card=target_card.to_dict(),
            )
            self._note(
                target,
                "trade",
                f"{actor.name} と取り引きし、「{target_card.label}」を渡して「{actor_card.label}」を受け取った。",
                player_id=actor.id,
                card=actor_card.to_dict(),
            )
            self._log("trade", f"{actor.name} と {target.name} がカードを交換した。")

        self.pending = None
        self.revision += 1
        self._after_action()

    def _after_detective(self) -> None:
        if self._detectives_exhausted():
            return
        self._after_action()

    def _detectives_exhausted(self) -> bool:
        if not self.config.culprit_wins_when_detectives_exhausted:
            return False
        if any(card.type is CardType.DETECTIVE for player in self.players for card in player.hand):
            return False
        self._finish_culprit_exhausted()
        return True

    def _after_action(self) -> None:
        if self.phase == FINISHED:
            return
        self._advance_turn()

    def _advance_turn(self) -> None:
        count = len(self.players)
        for step in range(1, count + 1):
            index = (self.current_index + step) % count
            candidate = self.players[index]
            if candidate.hand:
                self.current_index = index
                self.phase = TURN
                return
        self._finish("stalemate", [], [], "全員の手札がなくなり、勝敗がつかなかった。")

    def _validate_play(self, player: Player, card: Card, inert: bool) -> None:
        if self.phase != TURN:
            raise InvalidAction("いまはカードを出せません。")
        if self.players[self.current_index].id != player.id:
            raise InvalidAction("あなたの手番ではありません。")
        if card.type is CardType.CULPRIT and len(player.hand) != 1:
            raise InvalidAction("犯人は手札の最後の1枚のときだけ出せます。")
        if card.type is CardType.FIRST_FINDER:
            raise InvalidAction("第一発見者はゲーム開始時だけ使います。")
        if card.type is CardType.DETECTIVE and not inert:
            if player.turns_taken < self.config.detective_min_turn - 1:
                raise InvalidAction("探偵は2周目から使えます。")

    def _detective_is_inert(self, player: Player, card: Card) -> bool:
        if not self.config.detective_inert_with_only_detective_or_culprit:
            return False
        rest = [c for c in player.hand if c.id != card.id]
        has_culprit = any(c.type is CardType.CULPRIT for c in rest)
        return has_culprit and all(c.type in (CardType.DETECTIVE, CardType.CULPRIT) for c in rest)

    def _require_turn(self, player_id: str) -> None:
        if self.phase == FINISHED:
            raise InvalidAction("ゲームは終了しています。")
        if self.phase != TURN:
            raise InvalidAction("いまはカードを出せません。")
        if player_id not in self.by_id:
            raise InvalidAction("このゲームのプレイヤーではありません。")
        if self.players[self.current_index].id != player_id:
            raise InvalidAction("あなたの手番ではありません。")

    def _require_target(self, actor: Player, target_id: str | None) -> Player:
        if not target_id:
            raise InvalidAction("対象のプレイヤーを選んでください。")
        target = self.by_id.get(target_id)
        if target is None or target.id == actor.id:
            raise InvalidAction("対象のプレイヤーが不正です。")
        return target

    def _pick_index(self, target: Player, requested: int | None) -> int:
        if requested is None:
            return self.rng.randrange(len(target.hand))
        if not isinstance(requested, int) or isinstance(requested, bool):
            raise InvalidAction("カードの位置が不正です。")
        if not 0 <= requested < len(target.hand):
            raise InvalidAction("カードの位置が不正です。")
        return requested

    def _hand_card(self, player: Player, card_id: str) -> Card:
        card = next((c for c in player.hand if c.id == card_id), None)
        if card is None:
            raise InvalidAction("そのカードは手札にありません。")
        return card

    def _index_of(self, player: Player) -> int:
        return next(index for index, candidate in enumerate(self.players) if candidate is player)

    # -------------------------------------------------------------------- 終了

    def _finish_catch(self, actor: Player, holder: Player, source: str) -> None:
        accomplices = self.accomplices()
        accomplice_ids = {p.id for p in accomplices}
        if self.config.accomplice_catching_culprit_loses and actor.id in accomplice_ids:
            winners = [holder] + [p for p in accomplices if p.id != actor.id]
            losers = [actor]
            message = f"{actor.name} はたくらみを出していたため、指摘は失敗。犯人の勝ち。"
        else:
            winners = [actor]
            losers = [holder] + [p for p in accomplices if p.id != actor.id]
            if source == "dog":
                message = f"{actor.name} のいぬが {holder.name} の犯人カードを引き当てた！"
            else:
                message = f"{actor.name} が {holder.name} を犯人と見破った！"
        self._finish(f"{source}_catch", winners, losers, message)

    def _finish_culprit_play(self, actor: Player) -> None:
        winners = [actor] + self.accomplices()
        winner_ids = {p.id for p in winners}
        losers = [p for p in self.players if p.id not in winner_ids]
        self._finish("culprit_played", winners, losers, f"{actor.name} が最後の犯人カードを出した。犯人の勝ち！")

    def _finish_culprit_exhausted(self) -> None:
        holder = self.culprit_holder()
        if holder is None:
            self._finish("stalemate", [], [], "探偵が尽きたが、犯人カードが見つからない。")
            return
        winners = [holder] + self.accomplices()
        winner_ids = {p.id for p in winners}
        losers = [p for p in self.players if p.id not in winner_ids]
        self._finish("detective_exhausted", winners, losers, "探偵カードが尽きた。犯人の勝ち。")

    def _finish(self, reason: str, winners: list[Player], losers: list[Player], message: str) -> None:
        self.phase = FINISHED
        self.pending = None
        winner_ids = _unique_ids(winners)
        loser_ids = _unique_ids(losers)
        self.result = {
            "reason": reason,
            "winners": winner_ids,
            "losers": loser_ids,
            "message": message,
        }
        self.revision += 1
        self._log("result", message)

    # ------------------------------------------------------------------ 記録

    def _played(self, player: Player, card: Card) -> None:
        self.played.append(
            {
                "playerId": player.id,
                "playerName": player.name,
                "card": card.to_dict(),
            }
        )

    def _log(self, kind: str, message: str) -> None:
        self.events.append(Event(seq=len(self.events) + 1, kind=kind, message=message))

    def _note(self, player: Player, kind: str, message: str, **data: Any) -> None:
        player.notes.append(
            {
                "seq": len(player.notes) + 1,
                "kind": kind,
                "message": message,
                **data,
            }
        )


def _unique_ids(players: list[Player]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for player in players:
        if player.id not in seen:
            seen.add(player.id)
            result.append(player.id)
    return result
