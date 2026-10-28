"""リアルタイム通知（SSE）。

方針:
- ゲームの状態そのものは配信しない。「部屋が変わった」という合図だけを流す。
- 実際に送る内容は接続ごとに ``views`` で作り直す（情報漏洩を防ぐ）。
- ブラウザは EventSource で受信し、届いたら通常の state API と同じ形で描画する。

WebSocket を使わない理由は README / DESIGN を参照。ブローカのインターフェースは
差し替え可能なように、通知を「キューに 1 つ積む」だけにしている。
"""

from __future__ import annotations

import queue
import secrets
import threading
import time
from dataclasses import dataclass

TICKET_TTL_SECONDS = 60.0
HEARTBEAT_SECONDS = 20.0

_LOCK = threading.RLock()
_tickets: dict[str, "StreamTicket"] = {}
_subscribers: dict[str, set[queue.Queue]] = {}


@dataclass(frozen=True, slots=True)
class StreamTicket:
    room_id: str
    player_id: str
    expires_at: float


def issue_ticket(room_id: str, player_id: str) -> str:
    """EventSource 用の短命チケットを発行する。

    EventSource はヘッダを付けられないため、長命のトークンを URL に置かず、
    1 回だけ使えるチケットに差し替える。
    """
    now = time.monotonic()
    with _LOCK:
        _prune(now)
        ticket = secrets.token_urlsafe(24)
        _tickets[ticket] = StreamTicket(room_id, player_id, now + TICKET_TTL_SECONDS)
    return ticket


def redeem(ticket: str) -> StreamTicket | None:
    """チケットを消費する。期限切れ・使用済みなら None。"""
    with _LOCK:
        _prune(time.monotonic())
        return _tickets.pop(ticket, None)


def subscribe(room_id: str) -> queue.Queue:
    channel: queue.Queue = queue.Queue(maxsize=1)
    with _LOCK:
        _subscribers.setdefault(room_id, set()).add(channel)
    return channel


def unsubscribe(room_id: str, channel: queue.Queue) -> None:
    with _LOCK:
        channels = _subscribers.get(room_id)
        if channels is None:
            return
        channels.discard(channel)
        if not channels:
            _subscribers.pop(room_id, None)


def publish(room_id: str) -> int:
    """そのルームの購読者に「更新あり」を通知する。戻り値は通知先の数。"""
    with _LOCK:
        channels = list(_subscribers.get(room_id, ()))
    notified = 0
    for channel in channels:
        try:
            channel.put_nowait(None)
            notified += 1
        except queue.Full:
            # 未処理の通知がすでにある。まとめて 1 回で足りる。
            notified += 1
    return notified


def subscriber_count(room_id: str) -> int:
    with _LOCK:
        return len(_subscribers.get(room_id, ()))


def reset() -> None:
    """テスト用。"""
    with _LOCK:
        _tickets.clear()
        _subscribers.clear()


def _prune(now: float) -> None:
    expired = [ticket for ticket, info in _tickets.items() if info.expires_at <= now]
    for ticket in expired:
        del _tickets[ticket]
