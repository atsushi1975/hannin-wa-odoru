"""「犯人は踊る」を共通基盤に載せるための定義。

共通基盤（gameplatform）から見えるのは ``GAME`` だけ。
ルールの実装は cards / config / engine / views に閉じている。
"""

from __future__ import annotations

from typing import Any

from gameplatform.errors import ConfigurationError, InvalidAction
from gameplatform.games import GameDefinition, register

from .config import RuleConfig
from .engine import Game
from .views import player_view

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
)

register(GAME)
