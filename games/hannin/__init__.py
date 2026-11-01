"""「犯人は踊る」を共通基盤に載せるための定義。

共通基盤（gameplatform）から見えるのは ``GAME`` だけ。
ルールの実装は cards / config / engine / views に閉じている。
"""

from __future__ import annotations

import logging
import random
from typing import Any

from gameplatform.errors import ConfigurationError, InvalidAction
from gameplatform.games import GameDefinition, register

from .config import RuleConfig
from .cpu import DEFAULT_MEMORY_WINDOW, CulpritEstimator, choose_action
from .engine import Game
from .views import player_view

logger = logging.getLogger("hannin.cpu")

# CPU の手番を進める上限（万一の無限ループ防止）。1 ゲームの手数より十分大きい。
_MAX_CPU_STEPS = 5000

# ルーム作成時に外から変更してよい設定。人数（min/max）はロビーの定員と
# 一致させる必要があるため、ここでは変更させない。
_BOOL_FIELDS = {
    "detective_inert_with_only_detective_or_culprit",
    "culprit_wins_when_detectives_exhausted",
    "accomplice_catching_culprit_loses",
}
_INT_FIELDS = {
    "detective_min_turn": (1, 4),
}


def normalize_config(raw: Any) -> dict[str, Any]:
    """外部入力の設定を検証し、保存してよい形にして返す。

    未知のキーは黙って捨てず、エラーにする（設定の取り違えを防ぐ）。
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigurationError("設定はオブジェクトで指定してください。")

    normalized: dict[str, Any] = {}
    for key, value in raw.items():
        if key in _BOOL_FIELDS:
            if not isinstance(value, bool):
                raise ConfigurationError(f"{key} は true / false で指定してください。")
            normalized[key] = value
        elif key in _INT_FIELDS:
            low, high = _INT_FIELDS[key]
            if isinstance(value, bool) or not isinstance(value, int):
                raise ConfigurationError(f"{key} は整数で指定してください。")
            if not low <= value <= high:
                raise ConfigurationError(f"{key} は {low}〜{high} の範囲で指定してください。")
            normalized[key] = value
        else:
            raise ConfigurationError(f"不明な設定です: {key}")
    return normalized


def create_game(players: list[tuple[str, str]], config: dict[str, Any]) -> Game:
    return Game(players, config=RuleConfig(**config))


def act(game: Game, player_id: str, payload: dict[str, Any]) -> None:
    """クライアントの操作をゲームに渡す。入力の形はここで検証する。"""
    action_type = payload.get("type")
    if action_type == "play_card":
        card_id = payload.get("cardId")
        if not isinstance(card_id, str):
            raise InvalidAction("cardId が不正です。")
        target_player_id = payload.get("targetPlayerId")
        if target_player_id is not None and not isinstance(target_player_id, str):
            raise InvalidAction("targetPlayerId が不正です。")
        target_card_index = payload.get("targetCardIndex")
        if target_card_index is not None and (
            isinstance(target_card_index, bool) or not isinstance(target_card_index, int)
        ):
            raise InvalidAction("targetCardIndex が不正です。")
        game.play_card(
            player_id,
            card_id,
            target_player_id=target_player_id,
            target_card_index=target_card_index,
        )
    elif action_type == "select_card":
        card_id = payload.get("cardId")
        if not isinstance(card_id, str):
            raise InvalidAction("cardId が不正です。")
        game.submit_selection(player_id, card_id)
    else:
        raise InvalidAction("type が不正です。")


class CpuDriver:
    """CPU プレイヤーの手番を1手ずつ進める。

    ``step(game)`` は CPU が1手打てば True、打てなければ False を返す
    （人間の番・ゲーム終了・詰み）。待ち時間は呼び出し側（rooms）が管理する。
    """

    def __init__(self, game: Game, cpu_ids: list[str], config: dict[str, Any]) -> None:
        self.cpu_ids = list(cpu_ids)
        self.rule = RuleConfig(**config)
        self.rng = random.Random()
        self.estimators = {
            cpu_id: CulpritEstimator(cpu_id, memory_window=DEFAULT_MEMORY_WINDOW) for cpu_id in self.cpu_ids
        }
        self.steps = 0

    def step(self, game: Game) -> bool:
        if self.steps >= _MAX_CPU_STEPS:
            logger.warning("CPU の手数が上限 %d に達したため止めました。", _MAX_CPU_STEPS)
            return False
        views = {cpu_id: player_view(game, cpu_id) for cpu_id in self.cpu_ids}
        for cpu_id in self.cpu_ids:
            self.estimators[cpu_id].update(views[cpu_id])

        for cpu_id in self.cpu_ids:
            payload = choose_action(views[cpu_id], self.estimators[cpu_id], self.rng, self.rule)
            if payload is None:
                continue
            try:
                act(game, cpu_id, payload)
            except InvalidAction:
                logger.exception("CPU %s の行動が拒否されました: %s", cpu_id, payload)
                return False
            self.steps += 1
            return True

        self._warn_if_stuck(game, views)
        return False

    def _warn_if_stuck(self, game: Game, views: dict[str, dict[str, Any]]) -> None:
        if game.phase != "turn":
            return
        current = game.players[game.current_index].id
        if current in views:
            logger.warning("CPU %s は出せる札がなく、手番が進みません（詰み）。", current)


GAME = GameDefinition(
    key="hannin",
    title="犯人は踊る",
    description="カードを交換しながら、手札に隠れた犯人を探す推理ゲーム。",
    min_players=3,
    max_players=8,
    create=create_game,
    view=player_view,
    act=act,
    normalize_config=normalize_config,
    cpu_driver=CpuDriver,
)

register(GAME)
