"""プレイヤーごとの公開ビュー。

他人の手札・他人の private note を絶対に含めない。
"""

from __future__ import annotations

import copy
from typing import Any

from .engine import PENDING, TURN, Game


def player_view(game: Game, player_id: str) -> dict[str, Any]:
    if player_id not in game.by_id:
        raise KeyError("このゲームのプレイヤーではありません")
    me = game.by_id[player_id]

    pending_view = None
    if game.pending is not None:
        pending = game.pending
        is_required = player_id in pending.required
        has_selected = player_id in pending.selections
        pending_view = {
            "kind": pending.kind,
            "actorId": pending.actor_id,
            "targetId": pending.target_id,
            "requiredPlayerIds": list(pending.required),
            "waitingPlayerIds": [pid for pid in pending.required if pid not in pending.selections],
            "isRequired": is_required,
            "hasSelected": has_selected,
            "myOptions": [card.to_dict() for card in me.hand] if is_required and not has_selected else [],
        }

    current_player_id = None
    if game.phase in (TURN, PENDING):
        current_player_id = game.players[game.current_index].id

    return {
        "revision": game.revision,
        "phase": game.phase,
        "currentPlayerId": current_player_id,
        "startedBy": game.started_by,
        "players": [
            {
                "id": player.id,
                "name": player.name,
                "handCount": len(player.hand),
                "turnsTaken": player.turns_taken,
                "tricksPlayed": player.tricks_played,
                "isCurrent": player.id == current_player_id,
                "isMe": player.id == player_id,
            }
            for player in game.players
        ],
        "me": {
            "id": me.id,
            "hand": [card.to_dict() for card in me.hand],
            "notes": list(me.notes),
        },
        "played": list(game.played),
        "pending": pending_view,
        "events": [
            {"seq": event.seq, "kind": event.kind, "message": event.message, "data": copy.deepcopy(event.data)}
            for event in game.events
        ],
        "result": _result_view(game),
    }


def _result_view(game: Game) -> dict[str, Any] | None:
    if game.result is None:
        return None
    return {
        "reason": game.result["reason"],
        "message": game.result["message"],
        "winners": [
            {"id": pid, "name": game.by_id[pid].name} for pid in game.result["winners"]
        ],
        "losers": [
            {"id": pid, "name": game.by_id[pid].name} for pid in game.result["losers"]
        ],
    }
