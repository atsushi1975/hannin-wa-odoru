"""ゲーム非依存のルーム管理。

ルームは「どのゲームか」「設定」「参加者」「開始後のゲーム実体」だけを持つ。
ゲーム固有の処理は ``GameDefinition`` に委譲する。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from . import games, realtime, sessions
from .errors import RoomError

logger = logging.getLogger("gameplatform.rooms")

LOCK = threading.RLock()

# CPU が1手打つ前に待つ時間（秒）。人間が直前の手を見られるように空ける。
CPU_TURN_DELAY_SECONDS = 1.5

_rooms: dict[str, "Room"] = {}
_cpu_threads: dict[str, threading.Thread] = {}


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
    # CPU の手番を進める駆動器（CPU 参加つきで開始したときだけ入る）。
    cpu_driver: Any | None = None
    # CPU の手番を進めるスレッドが動いているか（LOCK 内で更新する）。
    cpu_running: bool = False

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


def start_game(room_id: str, token: str, cpu_count: int = 0) -> tuple[Room, str]:
    with LOCK:
        room, player_id = authenticate(room_id, token)
        definition = room.definition
        if player_id != room.host_id:
            raise RoomError("開始できるのはホストだけです。", status=403)
        if room.started:
            raise RoomError("すでに開始しています。")
        if cpu_count and definition.cpu_driver is None:
            raise RoomError("このゲームはCPUに対応していません。")
        total = len(room.players) + cpu_count
        if total > definition.max_players:
            raise RoomError(f"CPUを含めて{definition.max_players}人までです。")
        if total < definition.min_players:
            raise RoomError(f"{definition.min_players}人以上で開始できます。")
        cpus = _new_cpus(room, cpu_count)
        game = definition.create(
            [(player["id"], player["name"]) for player in [*room.players, *cpus]],
            dict(room.config),
        )
        try:
            game.start()
        except ValueError as exc:
            raise RoomError(str(exc)) from exc
        room.players.extend(cpus)
        room.game = game
        if cpus:
            room.cpu_driver = definition.cpu_driver(game, [cpu["id"] for cpu in cpus], dict(room.config))
        room.version += 1
        kick_cpus(room)
        return room, player_id


def kick_cpus(room: Room) -> None:
    """CPU の手番を別スレッドで進め始める（LOCK 内で呼ぶ）。動いていれば何もしない。

    人間の操作のあとや開始直後に呼ぶ。1手ごとに ``CPU_TURN_DELAY_SECONDS`` 待ち、
    待ちの間はロックを持たない。CPU が打てなくなったらスレッドは終わる。
    """
    if room.cpu_driver is None or room.cpu_running:
        return
    room.cpu_running = True
    thread = threading.Thread(target=_run_cpus, args=(room.id,), daemon=True)
    _cpu_threads[room.id] = thread
    thread.start()


def _run_cpus(room_id: str) -> None:
    while True:
        time.sleep(CPU_TURN_DELAY_SECONDS)
        with LOCK:
            room = _rooms.get(room_id)
            if room is None or room.cpu_driver is None or room.game is None:
                return
            try:
                acted = room.cpu_driver.step(room.game)
            except Exception:
                logger.exception("CPU の手番処理で例外が起きたため止めました。 room=%s", room_id)
                acted = False
            if not acted:
                room.cpu_running = False
                return
        realtime.publish(room_id)


def wait_for_cpus(room_id: str, timeout: float = 10.0) -> None:
    """テスト用。CPU の手番スレッドが終わるまで待つ。"""
    thread = _cpu_threads.get(room_id)
    if thread is not None:
        thread.join(timeout)


def get_room(room_id: str) -> Room:
    room = _rooms.get(room_id)
    if room is None:
        raise RoomError("ルームが見つかりません。", status=404)
    return room


def find_room(room_id: str) -> Room | None:
    return _rooms.get(room_id)


def list_rooms() -> list[Room]:
    with LOCK:
        return list(_rooms.values())


_TERMINAL_PHASES = {"finished"}


def _is_finished(game: Any) -> bool:
    """ゲームが終了（勝敗が決いた）状態かどうか。

    ``games/hannin/engine.py`` の ``FINISHED``（``Game.phase == "finished"``、
    または勝敗が ``Game.result`` に入った時点）が終了の判定。
    終了済みルームは ``rooms._rooms`` に残るため、進行中から除外する。
    """
    if getattr(game, "result", None) is not None:
        return True
    return getattr(game, "phase", None) in _TERMINAL_PHASES


def count_rooms() -> tuple[int, int]:
    """「開始前」と「進行中」のルーム数を数える。

    戻り値は ``(waiting_count, playing_count)``。

    - waiting: 開始前（ゲーム未開始）のルームの数。
    - playing: 開始済みで、かつ未終了（勝敗が決まっていない）のルームの数。

    終了済み（``Game.phase == FINISHED``）のルームは ``rooms._rooms`` に残るため、
    ここでは進行中から除外する。
    """
    with LOCK:
        waiting = 0
        playing = 0
        for room in _rooms.values():
            if not room.started:
                waiting += 1
                continue
            if _is_finished(room.game):
                continue
            playing += 1
        return waiting, playing


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
        _cpu_threads.clear()
        sessions.reset()


def _new_cpus(room: Room, count: int) -> list[dict[str, str]]:
    """CPU 用のプレイヤー（トークンは持たない）。名前は既存の名前と重ならないように付ける。"""
    taken = {player["name"].casefold() for player in room.players}
    cpus = []
    number = 1
    for _ in range(count):
        while f"cpu{number}" in taken:
            number += 1
        taken.add(f"cpu{number}")
        cpus.append({"id": f"cpu-{uuid.uuid4().hex[:8]}", "name": f"CPU{number}"})
    return cpus


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
