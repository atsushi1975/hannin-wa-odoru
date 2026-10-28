from __future__ import annotations

import unittest

import games  # noqa: F401  （ゲーム登録）
from gameplatform import games as game_registry
from gameplatform import realtime, rooms, sessions
from gameplatform.errors import ConfigurationError, RoomError


class RegistryTest(unittest.TestCase):
    def test_hannin_is_registered(self):
        definition = game_registry.get("hannin")
        self.assertEqual(definition.title, "犯人は踊る")
        self.assertEqual((definition.min_players, definition.max_players), (3, 8))
        self.assertIn("hannin", [item.key for item in game_registry.catalog()])

    def test_unknown_game_is_rejected(self):
        with self.assertRaises(RoomError) as caught:
            game_registry.get("unknown")
        self.assertEqual(caught.exception.status, 404)


class RoomTest(unittest.TestCase):
    def setUp(self):
        rooms.reset()
        realtime.reset()

    def test_create_room_stores_game_and_config(self):
        room, player_id, token = rooms.create_room(
            "ホスト", game_key="hannin", config={"detective_min_turn": 3}
        )
        self.assertEqual(room.game_key, "hannin")
        self.assertEqual(room.host_id, player_id)
        self.assertEqual(room.config, {"detective_min_turn": 3})
        self.assertEqual(len(room.players), 1)
        public = room.public()
        self.assertEqual(public["gameTitle"], "犯人は踊る")
        self.assertFalse(public["started"])
        self.assertEqual(public["minPlayers"], 3)

    def test_config_is_validated(self):
        for config in (
            {"unknown": True},
            {"detective_min_turn": 0},
            {"detective_min_turn": "2"},
            {"culprit_wins_when_detectives_exhausted": 1},
            ["not", "an", "object"],
        ):
            with self.subTest(config=config):
                with self.assertRaises(ConfigurationError):
                    rooms.create_room("ホスト", config=config)

    def test_join_updates_version_and_capacity(self):
        room, _, _ = rooms.create_room("P1")
        version = room.version
        for index in range(2, 9):
            rooms.join_room(room.id, f"P{index}")
        self.assertGreater(room.version, version)
        self.assertEqual(len(room.players), 8)
        with self.assertRaises(RoomError):
            rooms.join_room(room.id, "P9")

    def test_start_requires_host_and_minimum_players(self):
        room, host_id, host_token = rooms.create_room("P1")
        _, guest_token = rooms.join_room(room.id, "P2")[1:]

        with self.assertRaises(RoomError) as caught:
            rooms.start_game(room.id, guest_token)
        self.assertEqual(caught.exception.status, 403)
        with self.assertRaises(RoomError):
            rooms.start_game(room.id, host_token)

        rooms.join_room(room.id, "P3")
        started, player_id = rooms.start_game(room.id, host_token)
        self.assertTrue(started.started)
        self.assertEqual(player_id, host_id)
        self.assertEqual(started.game.config.detective_min_turn, 2)

    def test_room_config_reaches_the_game(self):
        room, _, host_token = rooms.create_room(
            "P1", config={"detective_min_turn": 3}
        )
        rooms.join_room(room.id, "P2")
        rooms.join_room(room.id, "P3")
        rooms.start_game(room.id, host_token)
        self.assertEqual(room.game.config.detective_min_turn, 3)

    def test_leave_transfers_host_and_revokes_token(self):
        room, host_id, host_token = rooms.create_room("P1")
        _, guest_id, guest_token = rooms.join_room(room.id, "P2")

        remaining, player_id, host_changed = rooms.leave_room(room.id, host_token)
        self.assertIsNotNone(remaining)
        self.assertEqual(player_id, host_id)
        self.assertTrue(host_changed)
        self.assertEqual(remaining.host_id, guest_id)
        with self.assertRaises(RoomError):
            rooms.authenticate(room.id, host_token)
        self.assertEqual(rooms.authenticate(room.id, guest_token)[1], guest_id)

    def test_last_player_leaving_closes_room(self):
        room, _, token = rooms.create_room("P1")
        remaining, _, _ = rooms.leave_room(room.id, token)
        self.assertIsNone(remaining)
        with self.assertRaises(RoomError):
            rooms.get_room(room.id)

    def test_cannot_leave_after_start(self):
        room, _, host_token = rooms.create_room("P1")
        rooms.join_room(room.id, "P2")
        rooms.join_room(room.id, "P3")
        rooms.start_game(room.id, host_token)
        with self.assertRaises(RoomError):
            rooms.leave_room(room.id, host_token)

    def test_host_takeover_unsticks_a_room(self):
        room, host_id, host_token = rooms.create_room("P1")
        _, guest_id, guest_token = rooms.join_room(room.id, "P2")

        updated, player_id = rooms.take_host(room.id, guest_token)
        self.assertEqual(player_id, guest_id)
        self.assertEqual(updated.host_id, guest_id)

        rooms.join_room(room.id, "P3")
        started, _ = rooms.start_game(room.id, guest_token)
        self.assertTrue(started.started)

        # 開始後は引き継げない（ホストのトークンもまだ有効）。
        with self.assertRaises(RoomError):
            rooms.take_host(room.id, host_token)


class SessionTest(unittest.TestCase):
    def setUp(self):
        sessions.reset()

    def test_token_is_bound_to_room_and_player(self):
        token = sessions.issue("room-a", "player-1")
        self.assertEqual(sessions.resolve_for_room(token, "room-a").player_id, "player-1")
        self.assertIsNone(sessions.resolve_for_room(token, "room-b"))

    def test_revoke_player_removes_every_token(self):
        first = sessions.issue("room-a", "player-1")
        second = sessions.issue("room-a", "player-1")
        other = sessions.issue("room-a", "player-2")
        self.assertEqual(sessions.revoke_player("room-a", "player-1"), 2)
        self.assertIsNone(sessions.resolve(first))
        self.assertIsNone(sessions.resolve(second))
        self.assertIsNotNone(sessions.resolve(other))


class RealtimeTest(unittest.TestCase):
    def setUp(self):
        realtime.reset()

    def test_ticket_is_single_use(self):
        ticket = realtime.issue_ticket("room-a", "player-1")
        info = realtime.redeem(ticket)
        self.assertEqual((info.room_id, info.player_id), ("room-a", "player-1"))
        self.assertIsNone(realtime.redeem(ticket))

    def test_publish_notifies_subscribers_only(self):
        channel = realtime.subscribe("room-a")
        self.assertEqual(realtime.subscriber_count("room-a"), 1)
        self.assertEqual(realtime.publish("room-a"), 1)
        self.assertIsNone(channel.get_nowait())
        self.assertEqual(realtime.publish("room-b"), 0)
        realtime.unsubscribe("room-a", channel)
        self.assertEqual(realtime.publish("room-a"), 0)


if __name__ == "__main__":
    unittest.main()
