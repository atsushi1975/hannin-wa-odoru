"""ゲーム非依存のルーム管理。

ルームは「どのゲームか」「設定」「参加者」「開始後のゲーム実体」だけを持つ。
ゲーム固有の処理は ``GameDefinition`` に委譲する。
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from . import games, sessions
from .errors import RoomError

LOCK = threading.RLock()

_rooms: dict[str, "Room"] = {}


@dataclass(slots=True)
class Room:
    id: str
    game_key: str
    host_id: str
    players: list[dict[str, str]] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)
    game: Any | None = None
    # ロビーの変化（入室・退出・開始）を検知するための版番号。
    version: int = 0

    @property
    def started(self) -> bool:
        return self.game is not None

    @property
    def definition(self) -> games.GameDefinition:
        return games.get(self.game_key)

    def player(self, player_id: str) -> dict[str, str] | None:
        return next((player for player in self.players if player["id"] == player_id), None)

    def public(self) -> dict[str, Any]:
        definition = self.definition
        return {
            "id": self.id,
            "gameKey": self.game_key,
            "gameTitle": definition.title,
            "gameDescription": definition.description,
            "started": self.started,
            "hostId": self.host_id,
            "players": [{"id": p["id"], "name": p["name"]} for p in self.players],
            "minPlayers": definition.min_players,
            "maxPlayers": definition.max_players,
            "config": dict(self.config),
        }


def create_room(
    name: str, *, game_key: str | None = None, config: Any = None
) -> tuple[Room, str, str]:
    with LOCK:
        key = game_key or games.default_key()
        definition = games.get(key)
        normalized = definition.normalize_config(config)
        room = Room(id=uuid.uuid4().hex[:8], game_key=key, host_id="", config=normalized)
        player_id, token = _add_player(room, name)
        room.host_id = player_id
        room.version = 1
        _rooms[room.id] = room
        return room, player_id, token


def join_room(room_id: str, name: str) -> tuple[Room, str, str]:
    room = get_room(room_id)
    with LOCK:
        if room.started:
            raise RoomError("このルームはすでに開始しています。")
        if len(room.players) >= room.definition.max_players:
            raise RoomError("このルームは満員です。")
        player_id, token = _add_player(room, name)
        room.version += 1
    return room, player_id, token


def leave_room(room_id: str, token: str) -> tuple[Room | None, str, bool]:
    """退出する。戻り値は (ルーム or 消滅なら None, プレイヤー id, ホスト交代の有無)。"""
    with LOCK:
        room, player_id = authenticate(room_id, token)
        if room.started:
            raise RoomError("ゲーム中は退出できません。")
        sessions.revoke_player(room_id, player_id)
        room.players = [player for player in room.players if player["id"] != player_id]
        room.version += 1
        if not room.players:
            del _rooms[room.id]
            return None, player_id, False
        host_changed = room.host_id == player_id
        if host_changed:
            room.host_id = room.players[0]["id"]
        return room, player_id, host_changed


def take_host(room_id: str, token: str) -> tuple[Room, str]:
    """ホスト権を引き継ぐ。

    ブラウザの保存情報が消えた／別プレイヤーで入り直した等でホストが
    操作できなくなると、誰も開始できずルームが詰む。開始前なら
    参加者なら誰でも引き継げるようにして復帰できるようにする。
    """
    with LOCK:
        room, player_id = authenticate(room_id, token)
        if room.started:
            raise RoomError("ゲーム中はホストを変更できません。")
        room.host_id = player_id
        room.version += 1
        return room, player_id


def start_game(room_id: str, token: str) -> tuple[Room, str]:
    with LOCK:
        room, player_id = authenticate(room_id, token)
        definition = room.definition
        if player_id != room.host_id:
            raise RoomError("開始できるのはホストだけです。", status=403)
        if room.started:
            raise RoomError("すでに開始しています。")
        if len(room.players) < definition.min_players:
            raise RoomError(f"{definition.min_players}人以上で開始できます。")
        game = definition.create(
            [(player["id"], player["name"]) for player in room.players],
            dict(room.config),
        )
        try:
            game.start()
        except ValueError as exc:
            raise RoomError(str(exc)) from exc
        room.game = game
        room.version += 1
        return room, player_id


def get_room(room_id: str) -> Room:
    room = _rooms.get(room_id)
    if room is None:
        raise RoomError("ルームが見つかりません。", status=404)
    return room


def find_room(room_id: str) -> Room | None:
    return _rooms.get(room_id)


def authenticate(room_id: str, token: str) -> tuple[Room, str]:
    room = get_room(room_id)
    session = sessions.resolve_for_room(token, room.id)
    if session is None or room.player(session.player_id) is None:
        raise RoomError("認証に失敗しました。", status=403)
    return room, session.player_id


def reset() -> None:
    """テスト用。"""
    with LOCK:
        _rooms.clear()
        sessions.reset()


def _add_player(room: Room, name: str) -> tuple[str, str]:
    cleaned = (name or "").strip()
    if not cleaned:
        raise RoomError("名前を入力してください。")
    if len(cleaned) > 16:
        raise RoomError("名前は16文字までです。")
    if any(player["name"].casefold() == cleaned.casefold() for player in room.players):
        raise RoomError("同じ名前のプレイヤーがいます。")
    player_id = uuid.uuid4().hex[:8]
    room.players.append({"id": player_id, "name": cleaned})
    token = sessions.issue(room.id, player_id)
    return player_id, token
