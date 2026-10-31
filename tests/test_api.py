from __future__ import annotations

import unittest
from unittest.mock import patch

from games.hannin.engine import FINISHED
from gameplatform import realtime, rooms
from health_app import create_health_app, hannin_status
from web import create_app
from web.api import _health_version

SAFE_TYPES = {"alibi", "trick", "info_op", "rumor", "boy", "civilian", "dog", "witness", "trade"}
TARGET_TYPES = {"dog", "witness", "trade"}


class ApiTest(unittest.TestCase):
    def setUp(self):
        rooms.reset()
        realtime.reset()
        self.app = create_app({"TESTING": True})

    def client(self):
        return self.app.test_client()

    def create_room(self, name: str):
        client = self.client()
        response = client.post("/api/rooms", json={"name": name})
        self.assertEqual(response.status_code, 201, response.get_json())
        data = response.get_json()
        return client, data

    def join_room(self, room_id: str, name: str):
        client = self.client()
        response = client.post(f"/api/rooms/{room_id}/join", json={"name": name})
        self.assertEqual(response.status_code, 200, response.get_json())
        return client, response.get_json()

    @staticmethod
    def headers(token: str) -> dict[str, str]:
        return {"X-Player-Token": token}

    def start(self, client, room_id: str, token: str):
        response = client.post(f"/api/rooms/{room_id}/start", headers=self.headers(token), json={})
        return response

    def state(self, client, room_id: str, token: str):
        response = client.get(f"/api/rooms/{room_id}/state", headers=self.headers(token))
        return response

    def three_players(self):
        host_client, host = self.create_room("ホスト")
        room_id = host["roomId"]
        client_b, player_b = self.join_room(room_id, "B")
        client_c, player_c = self.join_room(room_id, "C")
        tokens = {
            host["playerId"]: host["token"],
            player_b["playerId"]: player_b["token"],
            player_c["playerId"]: player_c["token"],
        }
        clients = {
            host["playerId"]: host_client,
            player_b["playerId"]: client_b,
            player_c["playerId"]: client_c,
        }
        return room_id, host_client, host, tokens, clients

    def test_room_lifecycle_and_private_hands(self):
        room_id, host_client, host, tokens, clients = self.three_players()

        # ホスト判定はクライアントに推測させず、サーバーが返す。
        host_state = self.state(host_client, room_id, host["token"]).get_json()
        self.assertEqual(host_state["you"], {"id": host["playerId"], "isHost": True})
        guest_id = next(pid for pid in tokens if pid != host["playerId"])
        guest_state = self.state(clients[guest_id], room_id, tokens[guest_id]).get_json()
        self.assertEqual(guest_state["you"], {"id": guest_id, "isHost": False})

        response = self.start(host_client, room_id, host["token"])
        self.assertEqual(response.status_code, 200, response.get_json())

        for player_id, token in tokens.items():
            response = self.state(clients[player_id], room_id, token)
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertTrue(payload["room"]["started"])
            game = payload["game"]
            self.assertEqual(game["me"]["id"], player_id)
            self.assertEqual(len(game["players"]), 3)
            for entry in game["players"]:
                self.assertNotIn("hand", entry)
            total = sum(entry["handCount"] for entry in game["players"])
            self.assertEqual(total, 11)  # 12枚配って第一発見者が1枚出す

    def test_non_host_cannot_start_and_minimum_players_is_enforced(self):
        host_client, host = self.create_room("ホスト")
        room_id = host["roomId"]
        client_b, player_b = self.join_room(room_id, "B")

        self.assertEqual(self.start(client_b, room_id, player_b["token"]).status_code, 403)
        self.assertEqual(self.start(host_client, room_id, host["token"]).status_code, 400)

    def test_join_after_start_is_rejected(self):
        room_id, host_client, host, tokens, clients = self.three_players()
        self.assertEqual(self.start(host_client, room_id, host["token"]).status_code, 200)

        late = self.client()
        response = late.post(f"/api/rooms/{room_id}/join", json={"name": "D"})
        self.assertEqual(response.status_code, 400)

    def test_bad_token_and_other_room_token_are_rejected(self):
        room_id, host_client, host, tokens, clients = self.three_players()
        self.assertEqual(self.state(host_client, room_id, "bad-token").status_code, 403)

        other_client, other = self.create_room("別室")
        response = self.state(other_client, room_id, other["token"])
        self.assertEqual(response.status_code, 403)

    def test_stale_revision_is_rejected(self):
        room_id, host_client, host, tokens, clients = self.three_players()
        self.assertEqual(self.start(host_client, room_id, host["token"]).status_code, 200)

        states = {}
        for player_id, token in tokens.items():
            payload = self.state(clients[player_id], room_id, token).get_json()
            states[player_id] = payload["game"]
        current_id = states[host["playerId"]]["currentPlayerId"]
        view = states[current_id]
        self.assertEqual(view["phase"], "turn")

        card = next(card for card in view["me"]["hand"] if card["type"] in SAFE_TYPES)
        payload = {
            "type": "play_card",
            "cardId": card["id"],
            "expectedRevision": view["revision"],
        }
        if card["type"] in TARGET_TYPES:
            target = next(
                player
                for player in view["players"]
                if player["id"] != current_id and player["handCount"] > 0
            )
            payload["targetPlayerId"] = target["id"]
            if card["type"] == "dog":
                payload["targetCardIndex"] = 0

        actor_client = clients[current_id]
        actor_token = tokens[current_id]
        response = actor_client.post(
            f"/api/rooms/{room_id}/actions", headers=self.headers(actor_token), json=payload
        )
        self.assertEqual(response.status_code, 200, response.get_json())

        stale = actor_client.post(
            f"/api/rooms/{room_id}/actions", headers=self.headers(actor_token), json=payload
        )
        self.assertEqual(stale.status_code, 409)

    def test_invalid_card_is_a_bad_request(self):
        room_id, host_client, host, tokens, clients = self.three_players()
        self.assertEqual(self.start(host_client, room_id, host["token"]).status_code, 200)

        view = self.state(host_client, room_id, host["token"]).get_json()["game"]
        current_id = view["currentPlayerId"]
        current_view = self.state(clients[current_id], room_id, tokens[current_id]).get_json()["game"]
        response = clients[current_id].post(
            f"/api/rooms/{room_id}/actions",
            headers=self.headers(tokens[current_id]),
            json={
                "type": "play_card",
                "cardId": "does-not-exist",
                "expectedRevision": current_view["revision"],
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_blank_name_is_rejected(self):
        response = self.client().post("/api/rooms", json={"name": "   "})
        self.assertEqual(response.status_code, 400)

    def test_games_catalog(self):
        response = self.client().get("/api/games")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["default"], "hannin")
        keys = [game["key"] for game in payload["games"]]
        self.assertIn("hannin", keys)
        hannin = next(game for game in payload["games"] if game["key"] == "hannin")
        self.assertEqual(hannin["minPlayers"], 3)
        self.assertEqual(hannin["maxPlayers"], 8)

    def test_unknown_game_and_invalid_config_are_rejected(self):
        response = self.client().post(
            "/api/rooms", json={"name": "A", "game": "unknown"}
        )
        self.assertEqual(response.status_code, 404)
        response = self.client().post(
            "/api/rooms",
            json={"name": "A", "game": "hannin", "config": {"oops": True}},
        )
        self.assertEqual(response.status_code, 400)

    def test_leave_transfers_host(self):
        host_client, host = self.create_room("ホスト")
        room_id = host["roomId"]
        client_b, player_b = self.join_room(room_id, "B")

        response = host_client.post(
            f"/api/rooms/{room_id}/leave", headers=self.headers(host["token"])
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["hostChanged"])

        info = self.client().get(f"/api/rooms/{room_id}").get_json()
        self.assertEqual(info["hostId"], player_b["playerId"])
        self.assertEqual(self.state(host_client, room_id, host["token"]).status_code, 403)
        self.assertEqual(
            self.state(client_b, room_id, player_b["token"]).status_code, 200
        )

    def test_stream_ticket_requires_authentication(self):
        _, host = self.create_room("ホスト")
        room_id = host["roomId"]
        response = self.client().post(f"/api/rooms/{room_id}/stream-ticket")
        self.assertEqual(response.status_code, 403)

    def test_guest_can_take_host_before_start(self):
        _, host = self.create_room("ホスト")
        room_id = host["roomId"]
        client_b, player_b = self.join_room(room_id, "B")

        response = client_b.post(
            f"/api/rooms/{room_id}/host", headers=self.headers(player_b["token"]), json={}
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(response.get_json()["you"]["isHost"])

        info = self.client().get(f"/api/rooms/{room_id}").get_json()
        self.assertEqual(info["hostId"], player_b["playerId"])

        response = self.start(client_b, room_id, player_b["token"])
        self.assertEqual(response.status_code, 400)  # まだ2人

    def test_health(self):
        response = self.client().get("/api/health")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertIn("hannin", payload["games"])

    def test_health_status_lifecycle(self):
        # --- idle: ルームが無い（または終了済みしかない）状態 ---
        response = self.client().get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "idle")

        # --- waiting: 開始前のルームが 1 つある ---
        room_id, host_client, host, tokens, clients = self.three_players()
        response = self.client().get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "waiting")

        # --- playing: 開始済みのルームが進行中 ---
        response = self.start(host_client, room_id, host["token"])
        self.assertEqual(response.status_code, 200)
        response = self.client().get("/health")
        self.assertEqual(response.get_json()["status"], "playing")

        # --- 終了後: 終了済みルームは rooms._rooms に残るが、進行中ではないので idle ---
        room = rooms.get_room(room_id)
        room.game.phase = FINISHED  # テスト用: 勝敗が決いた状態（engine の FINISHED）
        response = self.client().get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "idle")

    def test_health_and_api_health_return_the_same_content(self):
        root_response = self.client().get("/health")
        api_response = self.client().get("/api/health")
        self.assertEqual(root_response.status_code, 200)
        self.assertEqual(api_response.status_code, 200)
        self.assertEqual(root_response.get_json(), api_response.get_json())
        payload = api_response.get_json()
        self.assertEqual(
            set(payload),
            {"ok", "games", "title", "version", "status"},
        )
        self.assertEqual(payload["status"], "idle")

    def test_health_version_falls_back_to_dev(self):
        """VERSION file missing -> version is 'dev' (cached once per process)."""
        with patch("web.api.APP_ROOT", "/nonexistent-path-for-version-test"):
            with patch("web.api.HEALTH_VERSION_CACHE", None):
                self.assertEqual(_health_version(), "dev")
                self.assertEqual(self.client().get("/health").get_json()["version"], "dev")
                self.assertEqual(self.client().get("/api/health").get_json()["version"], "dev")

        version = _health_version()
        self.assertTrue(isinstance(version, str) and version != "")

    def test_standalone_health_app_returns_the_common_payload(self):
        standalone = create_health_app("犯人は踊る", hannin_status)
        client = standalone.test_client()
        response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(set(payload), {"title", "version", "status"})
        self.assertEqual(payload["title"], "犯人は踊る")
        self.assertEqual(payload["version"], _health_version())
        self.assertEqual(payload["status"], "idle")

    def test_standalone_health_app_reflects_room_states(self):
        standalone = create_health_app("犯人は踊る", hannin_status)
        client = standalone.test_client()
        room_id, host_client, host, tokens, clients = self.three_players()
        self.assertEqual(client.get("/health").get_json()["status"], "waiting")
        self.assertEqual(self.start(host_client, room_id, host["token"]).status_code, 200)
        self.assertEqual(client.get("/health").get_json()["status"], "playing")
        room = rooms.get_room(room_id)
        room.game.phase = FINISHED
        self.assertEqual(client.get("/health").get_json()["status"], "idle")
