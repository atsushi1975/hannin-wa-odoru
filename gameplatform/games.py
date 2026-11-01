"""ゲーム登録。

新しいゲーム（UNO・大富豪など）は ``GameDefinition`` を作って登録するだけで、
ルーム管理・API・リアルタイム通知をそのまま使える。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .errors import RoomError


@runtime_checkable
class GameLike(Protocol):
    """ルームが要求する最小限のゲームの形。"""

    revision: int
    phase: str

    def start(self) -> None: ...


@dataclass(frozen=True, slots=True)
class GameDefinition:
    key: str
    title: str
    description: str
    min_players: int
    max_players: int
    # 参加者 (id, name) と正規化済み設定からゲームを作る。
    create: Callable[[list[tuple[str, str]], dict[str, Any]], Any]
    # (game, player_id) -> その人に見せてよい状態。
    view: Callable[[Any, str], dict[str, Any]]
    # (game, player_id, action_payload) -> 状態を進める。不正なら InvalidAction。
    act: Callable[[Any, str, dict[str, Any]], None]
    # 外部入力を検証し、保存してよい設定だけを返す。
    normalize_config: Callable[[Any], dict[str, Any]]
    # (game, cpu_ids, normalized_config) -> CPU の手番を1手ずつ進める駆動器（step(game) -> bool）。
    # None なら CPU 参加に対応しない。
    cpu_driver: Callable[[Any, list[str], dict[str, Any]], Any] | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "description": self.description,
            "minPlayers": self.min_players,
            "maxPlayers": self.max_players,
        }


_REGISTRY: dict[str, GameDefinition] = {}


def register(definition: GameDefinition) -> None:
    if definition.key in _REGISTRY:
        raise ValueError(f"ゲームキーが重複しています: {definition.key}")
    if definition.min_players < 1 or definition.max_players < definition.min_players:
        raise ValueError(f"人数設定が不正です: {definition.key}")
    _REGISTRY[definition.key] = definition


def get(key: str) -> GameDefinition:
    try:
        return _REGISTRY[key]
    except KeyError:
        raise RoomError(f"ゲームが見つかりません: {key}", status=404) from None


def is_registered(key: str) -> bool:
    return key in _REGISTRY


def catalog() -> list[GameDefinition]:
    return sorted(_REGISTRY.values(), key=lambda definition: definition.title)


def default_key() -> str:
    definition = next(iter(_REGISTRY.values()), None)
    if definition is None:
        raise RuntimeError("ゲームが 1 つも登録されていません")
    return definition.key
