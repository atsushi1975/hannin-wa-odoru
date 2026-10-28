"""JSON API。HTTP の都合はここだけに閉じ込める。"""

from __future__ import annotations

import json
import queue
from typing import Any

from flask import Blueprint, Response, jsonify, request, stream_with_context

from gameplatform import games, realtime, rooms
from gameplatform.errors import InvalidAction, PlatformError

api = Blueprint("api", __name__, url_prefix="/api")


def _token() -> str:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[len("Bearer ") :].strip()
    return request.headers.get("X-Player-Token", "").strip()


def _room_state(room: rooms.Room, player_id: str) -> dict[str, Any]:
    game_view = None
    if room.game is not None:
        game_view = room.definition.view(room.game, player_id)
    return {
        "room": room.public(),
        "roomVersion": room.version,
        # クライアントにホスト判定を推測させない（トークンと部屋の対応はサーバーが知っている）。
        "you": {"id": player_id, "isHost": room.host_id == player_id},
        "game": game_view,
    }


def _error(message: str, status: int = 400, **extra: Any):
    return jsonify({"error": message, **extra}), status


@api.get("/games")
def list_games():
    return jsonify(
        {
            "default": games.default_key(),
            "games": [
                {**definition.summary(), "defaultConfig": definition.normalize_config({})}
                for definition in games.catalog()
            ],
        }
    )


@api.post("/rooms")
def create_room():
    data = request.get_json(silent=True) or {}
    try:
        with rooms.LOCK:
            room, player_id, token = rooms.create_room(
                data.get("name", ""),
                game_key=data.get("game") or games.default_key(),
                config=data.get("config"),
            )
    except PlatformError as exc:
        return _error(str(exc), exc.status)
    return (
        jsonify(
            {
                "roomId": room.id,
                "playerId": player_id,
                "token": token,
                "gameKey": room.game_key,
            }
        ),
        201,
    )


@api.post("/rooms/<room_id>/join")
def join_room(room_id: str):
    data = request.get_json(silent=True) or {}
    try:
        with rooms.LOCK:
            room, player_id, token = rooms.join_room(room_id, data.get("name", ""))
        realtime.publish(room_id)
    except PlatformError as exc:
        return _error(str(exc), exc.status)
    return jsonify({"roomId": room.id, "playerId": player_id, "token": token})


@api.post("/rooms/<room_id>/leave")
def leave_room(room_id: str):
    try:
        with rooms.LOCK:
            room, player_id, host_changed = rooms.leave_room(room_id, _token())
        realtime.publish(room_id)
    except PlatformError as exc:
        return _error(str(exc), exc.status)
    return jsonify(
        {
            "left": True,
            "playerId": player_id,
            "roomClosed": room is None,
            "hostChanged": host_changed,
        }
    )


@api.post("/rooms/<room_id>/host")
def take_host(room_id: str):
    """ホスト権を引き継ぐ（開始前のみ）。詰んだルームの復帰用。"""
    try:
        with rooms.LOCK:
            room, player_id = rooms.take_host(room_id, _token())
        realtime.publish(room_id)
        with rooms.LOCK:
            return jsonify(_room_state(room, player_id))
    except PlatformError as exc:
        return _error(str(exc), exc.status)


@api.get("/rooms/<room_id>")
def room_info(room_id: str):
    try:
        with rooms.LOCK:
            room = rooms.get_room(room_id)
            payload = room.public()
    except PlatformError as exc:
        return _error(str(exc), exc.status)
    return jsonify(payload)


@api.get("/rooms/<room_id>/state")
def room_state(room_id: str):
    try:
        with rooms.LOCK:
            room, player_id = rooms.authenticate(room_id, _token())
            return jsonify(_room_state(room, player_id))
    except PlatformError as exc:
        return _error(str(exc), exc.status)


@api.post("/rooms/<room_id>/start")
def start(room_id: str):
    try:
        with rooms.LOCK:
            room, player_id = rooms.start_game(room_id, _token())
        realtime.publish(room_id)
        with rooms.LOCK:
            return jsonify(_room_state(room, player_id))
    except PlatformError as exc:
        return _error(str(exc), exc.status)


@api.post("/rooms/<room_id>/actions")
def action(room_id: str):
    data = request.get_json(silent=True) or {}
    try:
        with rooms.LOCK:
            room, player_id = rooms.authenticate(room_id, _token())
            game = room.game
            if game is None:
                return _error("ゲームがまだ開始していません。")

            expected = data.get("expectedRevision")
            if expected is not None:
                if isinstance(expected, bool) or not isinstance(expected, int):
                    return _error("expectedRevision が不正です。")
                if expected != game.revision:
                    return _error(
                        "状態が更新されています。再読み込みしてください。",
                        409,
                        game=room.definition.view(game, player_id),
                    )

            try:
                room.definition.act(game, player_id, data)
            except InvalidAction as exc:
                return _error(
                    str(exc), 400, game=room.definition.view(game, player_id)
                )

            payload = _room_state(room, player_id)
        realtime.publish(room_id)
        return jsonify(payload)
    except PlatformError as exc:
        return _error(str(exc), exc.status)


@api.post("/rooms/<room_id>/stream-ticket")
def stream_ticket(room_id: str):
    """EventSource 用の短命チケットを発行する（認証はヘッダで行う）。"""
    try:
        with rooms.LOCK:
            _, player_id = rooms.authenticate(room_id, _token())
            ticket = realtime.issue_ticket(room_id, player_id)
    except PlatformError as exc:
        return _error(str(exc), exc.status)
    return jsonify({"ticket": ticket, "expiresIn": realtime.TICKET_TTL_SECONDS})


@api.get("/rooms/<room_id>/events")
def events(room_id: str):
    """SSE。状態そのものではなく「変わった」ことを知らせ、都度作り直して送る。"""
    info = realtime.redeem(request.args.get("ticket", ""))
    if info is None or info.room_id != room_id:
        return _error("ストリームの認証に失敗しました。", 403)

    def generate():
        channel = realtime.subscribe(room_id)
        try:
            yield ": connected\n\n"
            last_key: tuple[int, int] | None = None
            while True:
                with rooms.LOCK:
                    room = rooms.find_room(room_id)
                    if room is None or room.player(info.player_id) is None:
                        yield _sse("closed", {"reason": "room_closed"})
                        return
                    state = _room_state(room, info.player_id)

                game_revision = state["game"]["revision"] if state["game"] else 0
                key = (state["roomVersion"], game_revision)
                if key != last_key:
                    last_key = key
                    yield _sse("state", state)

                try:
                    channel.get(timeout=realtime.HEARTBEAT_SECONDS)
                except queue.Empty:
                    yield ": ping\n\n"
        finally:
            realtime.unsubscribe(room_id, channel)

    response = Response(stream_with_context(generate()), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@api.get("/health")
def health():
    return jsonify({"ok": True, "games": [definition.key for definition in games.catalog()]})
