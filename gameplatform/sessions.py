"""ゲストセッション。

ログイン機能は持たず、ルーム参加時に発行するトークンで本人を識別する。
トークンはルームとプレイヤーに 1 対 1 で結び付き、他ルームでは使えない。
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass

_LOCK = threading.RLock()
_sessions: dict[str, "Session"] = {}


@dataclass(frozen=True, slots=True)
class Session:
    token: str
    room_id: str
    player_id: str
    issued_at: float


def issue(room_id: str, player_id: str) -> str:
    """新しいトークンを発行する。"""
    token = secrets.token_urlsafe(24)
    with _LOCK:
        _sessions[token] = Session(token, room_id, player_id, time.time())
    return token


def resolve(token: str) -> Session | None:
    if not token:
        return None
    with _LOCK:
        return _sessions.get(token)


def resolve_for_room(token: str, room_id: str) -> Session | None:
    session = resolve(token)
    if session is None or session.room_id != room_id:
        return None
    return session


def revoke(token: str) -> None:
    with _LOCK:
        _sessions.pop(token, None)


def revoke_player(room_id: str, player_id: str) -> int:
    """そのルームのプレイヤーのトークンをすべて無効化する。"""
    with _LOCK:
        targets = [
            token
            for token, session in _sessions.items()
            if session.room_id == room_id and session.player_id == player_id
        ]
        for token in targets:
            del _sessions[token]
    return len(targets)


def reset() -> None:
    """テスト用。"""
    with _LOCK:
        _sessions.clear()
