from __future__ import annotations

import json
import unittest

from games.hannin.cards import CardType as T
from games.hannin.config import RuleConfig
from games.hannin.engine import FINISHED, PENDING, TURN, InvalidAction
from games.hannin.views import player_view
from tests.helpers import all_hand_ids, card_of, make_game, play


class StartTest(unittest.TestCase):
    def test_first_finder_starts_and_turn_moves_clockwise(self):
        game = make_game(
            [T.FIRST_FINDER, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
            [T.CIVILIAN] * 4,
            [T.CIVILIAN] * 4,
        )
        game.start()
        self.assertEqual(game.phase, TURN)
        self.assertEqual(game.started_by, "p1")
        self.assertEqual(len(game.players[0].hand), 3)
        self.assertEqual(game.played[0]["card"]["type"], T.FIRST_FINDER.value)
        self.assertEqual(game.players[game.current_index].id, "p2")

    def test_cannot_start_twice(self):
        game = make_game(
            [T.FIRST_FINDER, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
            [T.CIVILIAN] * 4,
            [T.CIVILIAN] * 4,
        )
        game.start()
        with self.assertRaises(InvalidAction):
            game.start()


class CulpritTest(unittest.TestCase):
    def test_culprit_cannot_be_played_before_last_card(self):
        game = make_game(
            [T.FIRST_FINDER, T.CIVILIAN, T.CIVILIAN, T.CULPRIT],
            [T.CIVILIAN] * 4,
            [T.TRICK, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
        )
        game.start()
        play(game, 1, T.CIVILIAN)
        play(game, 2, T.TRICK)
        with self.assertRaises(InvalidAction):
            play(game, 0, T.CULPRIT)

    def test_culprit_wins_with_accomplices(self):
        game = make_game(
            [T.FIRST_FINDER, T.CIVILIAN, T.CIVILIAN, T.CULPRIT],
            [T.CIVILIAN] * 4,
            [T.TRICK, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
        )
        game.start()
        play(game, 1, T.CIVILIAN)
        play(game, 2, T.TRICK)
        play(game, 0, T.CIVILIAN)
        play(game, 1, T.CIVILIAN)
        play(game, 2, T.CIVILIAN)
        play(game, 0, T.CIVILIAN)
        play(game, 1, T.CIVILIAN)
        play(game, 2, T.CIVILIAN)
        play(game, 0, T.CULPRIT)

        self.assertEqual(game.phase, FINISHED)
        self.assertEqual(game.result["reason"], "culprit_played")
        self.assertEqual(set(game.result["winners"]), {"p1", "p3"})
        self.assertEqual(set(game.result["losers"]), {"p2"})


class DetectiveTest(unittest.TestCase):
    def test_detective_cannot_be_used_on_first_turn(self):
        game = make_game(
            [T.FIRST_FINDER, T.DETECTIVE, T.CIVILIAN, T.CIVILIAN],
            [T.CULPRIT, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
            [T.CIVILIAN] * 4,
        )
        game.start()
        play(game, 1, T.CIVILIAN)
        play(game, 2, T.CIVILIAN)
        with self.assertRaises(InvalidAction):
            play(game, 0, T.DETECTIVE, target_player_id="p2")

    def test_detective_catches_culprit_on_second_turn(self):
        game = make_game(
            [T.FIRST_FINDER, T.DETECTIVE, T.CIVILIAN, T.CIVILIAN],
            [T.CULPRIT, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
            [T.CIVILIAN] * 4,
        )
        game.start()
        play(game, 1, T.CIVILIAN)
        play(game, 2, T.CIVILIAN)
        play(game, 0, T.CIVILIAN)
        play(game, 1, T.CIVILIAN)
        play(game, 2, T.CIVILIAN)
        play(game, 0, T.DETECTIVE, target_player_id="p2")

        self.assertEqual(game.phase, FINISHED)
        self.assertEqual(game.result["reason"], "detective_catch")
        self.assertEqual(game.result["winners"], ["p1"])
        self.assertEqual(set(game.result["losers"]), {"p2"})

    def test_alibi_blocks_detective(self):
        game = make_game(
            [T.FIRST_FINDER, T.DETECTIVE, T.CIVILIAN, T.CIVILIAN],
            [T.CULPRIT, T.ALIBI, T.CIVILIAN, T.CIVILIAN],
            [T.CIVILIAN] * 4,
        )
        game.start()
        play(game, 1, T.CIVILIAN)
        play(game, 2, T.CIVILIAN)
        play(game, 0, T.CIVILIAN)
        play(game, 1, T.CIVILIAN)
        play(game, 2, T.CIVILIAN)
        play(game, 0, T.DETECTIVE, target_player_id="p2")

        self.assertEqual(game.phase, TURN)
        self.assertIsNone(game.result)
        self.assertEqual(game.events[-1].kind, "detective_miss")

    def test_no_detectives_left_makes_culprit_win_when_enabled(self):
        config = RuleConfig(culprit_wins_when_detectives_exhausted=True)
        game = make_game(
            [T.FIRST_FINDER, T.DETECTIVE, T.CIVILIAN, T.CIVILIAN],
            [T.CULPRIT, T.ALIBI, T.CIVILIAN, T.CIVILIAN],
            [T.CIVILIAN] * 4,
            config=config,
        )
        game.start()
        play(game, 1, T.CIVILIAN)
        play(game, 2, T.CIVILIAN)
        play(game, 0, T.CIVILIAN)
        play(game, 1, T.CIVILIAN)
        play(game, 2, T.CIVILIAN)
        play(game, 0, T.DETECTIVE, target_player_id="p2")

        self.assertEqual(game.phase, FINISHED)
        self.assertEqual(game.result["reason"], "detective_exhausted")
        self.assertEqual(game.result["winners"], ["p2"])
        self.assertEqual(set(game.result["losers"]), {"p1", "p3"})

    def test_detectives_exhausted_does_not_end_game_by_default(self):
        # 既定では枯渇ルールを使わない。犯人を外した探偵が最後の1枚でも続行する。
        game = make_game(
            [T.FIRST_FINDER, T.DETECTIVE, T.CIVILIAN, T.CIVILIAN],
            [T.CULPRIT, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
            [T.CIVILIAN] * 4,
        )
        game.start()
        play(game, 1, T.CIVILIAN)
        play(game, 2, T.CIVILIAN)
        play(game, 0, T.CIVILIAN)
        play(game, 1, T.CIVILIAN)
        play(game, 2, T.CIVILIAN)
        play(game, 0, T.DETECTIVE, target_player_id="p3")

        self.assertEqual(game.phase, TURN)
        self.assertIsNone(game.result)
        self.assertEqual(game.events[-1].kind, "detective_miss")

    def test_accomplice_catching_culprit_loses(self):
        game = make_game(
            [T.FIRST_FINDER, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
            [T.TRICK, T.DETECTIVE, T.CIVILIAN, T.CIVILIAN],
            [T.CULPRIT, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
        )
        game.start()
        play(game, 1, T.TRICK)
        play(game, 2, T.CIVILIAN)
        play(game, 0, T.CIVILIAN)
        play(game, 1, T.DETECTIVE, target_player_id="p3")

        self.assertEqual(game.phase, FINISHED)
        self.assertEqual(game.result["winners"], ["p3"])
        self.assertEqual(game.result["losers"], ["p2"])


class DogTest(unittest.TestCase):
    def test_dog_revealing_culprit_wins(self):
        game = make_game(
            [T.FIRST_FINDER, T.DOG, T.CIVILIAN, T.CIVILIAN],
            [T.CULPRIT, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
            [T.CIVILIAN] * 4,
        )
        game.start()
        play(game, 1, T.CIVILIAN)
        play(game, 2, T.CIVILIAN)
        play(game, 0, T.DOG, target_player_id="p2", target_card_index=0)

        self.assertEqual(game.phase, FINISHED)
        self.assertEqual(game.result["reason"], "dog_catch")
        self.assertEqual(game.result["winners"], ["p1"])
        self.assertEqual(game.result["losers"], ["p2"])

    def test_dog_miss_returns_card_to_hand(self):
        game = make_game(
            [T.FIRST_FINDER, T.DOG, T.CIVILIAN, T.CIVILIAN],
            [T.CULPRIT, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
            [T.CIVILIAN] * 4,
        )
        game.start()
        play(game, 1, T.CIVILIAN)
        play(game, 2, T.CIVILIAN)
        dog = card_of(game, 0, T.DOG)
        before = set(all_hand_ids(game)) - {dog.id}
        play(game, 0, T.DOG, target_player_id="p2", target_card_index=1)

        self.assertEqual(game.phase, TURN)
        self.assertIsNone(game.result)
        self.assertEqual(len(game.players[1].hand), 3)
        self.assertEqual(set(all_hand_ids(game)), before)


class TradeTest(unittest.TestCase):
    def test_trade_swaps_selected_cards_and_keeps_ids(self):
        game = make_game(
            [T.FIRST_FINDER, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
            [T.TRADE, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
            [T.CIVILIAN] * 4,
        )
        game.start()
        play(game, 1, T.TRADE, target_player_id="p3")

        self.assertEqual(game.phase, PENDING)
        self.assertEqual(set(game.pending.required), {"p2", "p3"})
        p2_card = game.players[1].hand[0]
        p3_card = game.players[2].hand[0]
        before = set(all_hand_ids(game))

        game.submit_selection("p2", p2_card.id)
        self.assertEqual(game.phase, PENDING)
        game.submit_selection("p3", p3_card.id)

        self.assertEqual(game.phase, TURN)
        self.assertEqual(set(all_hand_ids(game)), before)
        self.assertIn(p3_card.id, {card.id for card in game.players[1].hand})
        self.assertIn(p2_card.id, {card.id for card in game.players[2].hand})
        self.assertTrue(any(note["kind"] == "trade" for note in game.players[1].notes))
        self.assertTrue(any(note["kind"] == "trade" for note in game.players[2].notes))
        self.assertEqual(game.players[game.current_index].id, "p3")


class SimultaneousTest(unittest.TestCase):
    def test_info_op_passes_every_card_exactly_once(self):
        game = make_game(
            [T.FIRST_FINDER, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
            [T.INFO_OP, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
            [T.CIVILIAN] * 4,
        )
        game.start()
        play(game, 1, T.INFO_OP)

        self.assertEqual(game.phase, PENDING)
        self.assertEqual(set(game.pending.required), {"p1", "p2", "p3"})
        p1_card = game.players[0].hand[0]
        p2_card = game.players[1].hand[0]
        p3_card = game.players[2].hand[0]
        all_ids = set(all_hand_ids(game)) | {game.played[-1]["card"]["id"]}

        game.submit_selection("p1", p1_card.id)
        game.submit_selection("p2", p2_card.id)
        game.submit_selection("p3", p3_card.id)

        self.assertEqual(game.phase, TURN)
        hands = [set(card.id for card in player.hand) for player in game.players]
        self.assertIn(p1_card.id, hands[1])  # p1 -> left neighbor p2
        self.assertIn(p2_card.id, hands[2])  # p2 -> p3
        self.assertIn(p3_card.id, hands[0])  # p3 -> p1
        self.assertEqual(set(all_hand_ids(game)) | {game.played[-1]["card"]["id"]}, all_ids)
        self.assertEqual([len(hand) for hand in hands], [3, 3, 4])
        self.assertEqual(game.players[game.current_index].id, "p3")

    def test_rumor_draws_from_right_neighbor_without_losing_cards(self):
        game = make_game(
            [T.FIRST_FINDER, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
            [T.RUMOR, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
            [T.CIVILIAN] * 4,
        )
        game.start()
        before = {player.id: {card.id for card in player.hand} for player in game.players}
        rumor = card_of(game, 1, T.RUMOR)
        play(game, 1, T.RUMOR)

        after = {player.id: {card.id for card in player.hand} for player in game.players}
        new_p1 = after["p1"] - before["p1"]
        new_p2 = after["p2"] - (before["p2"] - {rumor.id})
        new_p3 = after["p3"] - before["p3"]
        self.assertEqual(len(new_p1), 1)
        self.assertEqual(len(new_p2), 1)
        self.assertEqual(len(new_p3), 1)
        self.assertTrue(new_p1 <= before["p3"])
        self.assertTrue(new_p2 <= before["p1"])
        self.assertTrue(new_p3 <= before["p2"] - {rumor.id})
        self.assertEqual(
            set(all_hand_ids(game)) | {rumor.id},
            set().union(*before.values()),
        )
        for player in game.players:
            self.assertTrue(any(note["kind"] == "rumor_draw" for note in player.notes))
        self.assertEqual(game.players[game.current_index].id, "p3")


class ViewTest(unittest.TestCase):
    def test_boy_note_is_private_and_hands_are_hidden(self):
        game = make_game(
            [T.FIRST_FINDER, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
            [T.BOY, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
            [T.CULPRIT, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
        )
        game.start()
        play(game, 1, T.BOY)

        view2 = player_view(game, "p2")
        view1 = player_view(game, "p1")
        view3 = player_view(game, "p3")
        self.assertTrue(any(note["kind"] == "boy" for note in view2["me"]["notes"]))
        self.assertEqual(view1["me"]["notes"], [])
        self.assertEqual(view3["me"]["notes"], [])
        self.assertTrue(all("hand" not in player for player in view2["players"]))
        self.assertEqual(view2["players"][2]["handCount"], 4)

        serialized = json.dumps(view1, ensure_ascii=False)
        for card in game.players[2].hand:
            self.assertNotIn(card.id, serialized)

    def test_witness_cards_are_private(self):
        game = make_game(
            [T.FIRST_FINDER, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
            [T.WITNESS, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
            [T.CULPRIT, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
        )
        game.start()
        play(game, 1, T.WITNESS, target_player_id="p3")

        view2 = player_view(game, "p2")
        contested_note = next(note for note in view2["me"]["notes"] if note["kind"] == "witness")
        self.assertEqual(len(contested_note["cards"]), 4)
        self.assertEqual(player_view(game, "p1")["me"]["notes"], [])

    def test_dog_reveal_is_public_and_not_private(self):
        game = make_game(
            [T.FIRST_FINDER, T.DOG, T.CIVILIAN, T.CIVILIAN],
            [T.CULPRIT, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
            [T.CIVILIAN] * 4,
        )
        game.start()
        play(game, 1, T.CIVILIAN)
        play(game, 2, T.CIVILIAN)
        # 犯人は p2。p3 の2枚目（一般人）を暴くので外れる。
        play(game, 0, T.DOG, target_player_id="p3", target_card_index=1)

        # いぬが暴いたカードは全員のログに出る（private note には入れない）。
        for player_id in ("p1", "p2", "p3"):
            view = player_view(game, player_id)
            self.assertTrue(any(event["kind"] == "dog_reveal" for event in view["events"]))
            self.assertEqual(view["me"]["notes"], [])

    def test_pending_options_are_only_visible_to_required_players(self):
        game = make_game(
            [T.FIRST_FINDER, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
            [T.TRADE, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
            [T.CIVILIAN] * 4,
        )
        game.start()
        play(game, 1, T.TRADE, target_player_id="p3")

        view2 = player_view(game, "p2")
        view1 = player_view(game, "p1")
        self.assertTrue(view2["pending"]["isRequired"])
        self.assertEqual(len(view2["pending"]["myOptions"]), 3)
        self.assertFalse(view1["pending"]["isRequired"])
        self.assertEqual(view1["pending"]["myOptions"], [])

    def test_player_view_rejects_non_player(self):
        game = make_game(
            [T.FIRST_FINDER, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
            [T.CIVILIAN] * 4,
            [T.CIVILIAN] * 4,
        )
        game.start()
        with self.assertRaises(KeyError):
            player_view(game, "unknown")


class EventDataTest(unittest.TestCase):
    def _event(self, game, kind):
        view = player_view(game, "p1")
        return next(event for event in view["events"] if event["kind"] == kind)

    def test_events_without_data_have_empty_dict(self):
        game = make_game(
            [T.FIRST_FINDER, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
            [T.CIVILIAN] * 4,
            [T.CIVILIAN] * 4,
        )
        game.start()
        for event in player_view(game, "p1")["events"]:
            self.assertEqual(event["data"], {})

    def test_rumor_has_hand_counts_before_move(self):
        game = make_game(
            [T.FIRST_FINDER, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
            [T.RUMOR, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
            [T.CIVILIAN] * 4,
        )
        game.start()
        play(game, 1, T.RUMOR)
        # 出した後・引く前: p1=3, p2=3, p3=4
        self.assertEqual(self._event(game, "rumor")["data"], {"handCounts": {"p1": 3, "p2": 3, "p3": 4}})

    def test_info_op_has_hand_counts_before_move(self):
        game = make_game(
            [T.FIRST_FINDER, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
            [T.INFO_OP, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
            [T.CIVILIAN] * 4,
        )
        game.start()
        play(game, 1, T.INFO_OP)
        for player in game.players:
            game.submit_selection(player.id, player.hand[0].id)
        self.assertEqual(self._event(game, "info_op")["data"], {"handCounts": {"p1": 3, "p2": 3, "p3": 4}})

    def test_trade_has_actor_target_and_hand_counts(self):
        game = make_game(
            [T.FIRST_FINDER, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
            [T.TRADE, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
            [T.CIVILIAN] * 4,
        )
        game.start()
        play(game, 1, T.TRADE, target_player_id="p3")
        game.submit_selection("p2", game.players[1].hand[0].id)
        game.submit_selection("p3", game.players[2].hand[0].id)
        self.assertEqual(
            self._event(game, "trade")["data"],
            {"actorId": "p2", "targetId": "p3", "handCounts": {"p1": 3, "p2": 3, "p3": 4}},
        )

    def test_event_data_does_not_leak_card_contents(self):
        def rumor(game):
            play(game, 1, T.RUMOR)

        def info_op(game):
            play(game, 1, T.INFO_OP)
            for player in game.players:
                game.submit_selection(player.id, player.hand[0].id)

        def trade(game):
            play(game, 1, T.TRADE, target_player_id="p3")
            game.submit_selection("p2", game.players[1].hand[0].id)
            game.submit_selection("p3", game.players[2].hand[0].id)

        allowed_keys = {"handCounts", "actorId", "targetId"}
        for card_type, scenario in ((T.RUMOR, rumor), (T.INFO_OP, info_op), (T.TRADE, trade)):
            with self.subTest(card=card_type.value):
                game = make_game(
                    [T.FIRST_FINDER, T.CULPRIT, T.ALIBI, T.CIVILIAN],
                    [card_type, T.DETECTIVE, T.WITNESS, T.CIVILIAN],
                    [T.BOY, T.DOG, T.CIVILIAN, T.CIVILIAN],
                )
                game.start()
                scenario(game)
                all_cards = {card.id for player in game.players for card in player.hand}
                for player in game.players:
                    view = player_view(game, player.id)
                    with_data = [event for event in view["events"] if event["data"]]
                    self.assertEqual([event["kind"] for event in with_data], [card_type.value])
                    for event in view["events"]:
                        self.assertLessEqual(set(event["data"]), allowed_keys)
                        counts = event["data"].get("handCounts", {})
                        self.assertTrue(all(isinstance(n, int) for n in counts.values()))
                        serialized = json.dumps(event["data"], ensure_ascii=False)
                        for card_id in all_cards:
                            self.assertNotIn(card_id, serialized)
                        for known_type in T:
                            self.assertNotIn(known_type.value, serialized)


class RedealTest(unittest.TestCase):
    def _dealt(self, game):
        return {player.id: [card.id for card in player.hand] for player in game.players}

    def _assert_no_detective_only_hand(self, game):
        for player in game.players:
            self.assertFalse(
                player.hand and all(card.type is T.DETECTIVE for card in player.hand),
                f"{player.id} の手札が探偵だけ",
            )

    def test_finder_with_only_detectives_is_redealt(self):
        # 第一発見者を出した後の手札が探偵3枚だけ。
        game = make_game(
            [T.FIRST_FINDER, T.DETECTIVE, T.DETECTIVE, T.DETECTIVE],
            [T.CIVILIAN, T.CIVILIAN, T.CIVILIAN, T.CULPRIT],
            [T.CIVILIAN, T.ALIBI, T.TRADE, T.DOG],
        )
        game.start()
        self._assert_no_detective_only_hand(game)
        self.assertEqual(game.phase, TURN)
        self.assertEqual(len(set(all_hand_ids(game)) | {game.played[0]["card"]["id"]}), 12)

    def test_non_finder_with_four_detectives_is_redealt(self):
        game = make_game(
            [T.FIRST_FINDER, T.CIVILIAN, T.CIVILIAN, T.CIVILIAN],
            [T.DETECTIVE] * 4,
            [T.CIVILIAN, T.ALIBI, T.CULPRIT, T.DOG],
        )
        game.start()
        self._assert_no_detective_only_hand(game)

    def test_redeal_keeps_every_card_and_leaves_no_trace(self):
        game = make_game(
            [T.FIRST_FINDER, T.DETECTIVE, T.DETECTIVE, T.DETECTIVE],
            [T.CIVILIAN, T.CIVILIAN, T.CIVILIAN, T.CULPRIT],
            [T.CIVILIAN, T.ALIBI, T.TRADE, T.DOG],
        )
        expected_ids = {card.id for card in game._deck}
        game.start()

        dealt = set(all_hand_ids(game)) | {entry["card"]["id"] for entry in game.played}
        self.assertEqual(dealt, expected_ids)
        # 配り直しはログにも note にも revision にも出ない（通常の開始と同じ）。
        self.assertEqual([event.kind for event in game.events], ["start"])
        self.assertEqual(game.revision, 1)
        for player in game.players:
            self.assertEqual(player.notes, [])
        self.assertEqual(game.played[0]["card"]["type"], T.FIRST_FINDER.value)

    def test_normal_deal_is_not_shuffled(self):
        # 探偵と犯人だけの手札(効果なしで出せる)や、探偵が混ざる手札は配り直さない。
        game = make_game(
            [T.FIRST_FINDER, T.DETECTIVE, T.CIVILIAN, T.CIVILIAN],
            [T.DETECTIVE, T.DETECTIVE, T.DETECTIVE, T.CULPRIT],
            [T.CIVILIAN] * 4,
        )
        expected = [[card.id for card in game._deck[i * 4 : (i + 1) * 4]] for i in range(3)]
        game.start()
        expected[0].remove(game.played[0]["card"]["id"])
        self.assertEqual([[card.id for card in player.hand] for player in game.players], expected)

    def test_redeal_is_reproducible_with_a_seeded_rng(self):
        import random

        def deal(seed):
            from games.hannin.engine import Game
            from tests.helpers import deck_from_hands

            game = Game(
                [("p1", "P1"), ("p2", "P2"), ("p3", "P3")],
                rng=random.Random(seed),
                deck=deck_from_hands(
                    [T.FIRST_FINDER, T.DETECTIVE, T.DETECTIVE, T.DETECTIVE],
                    [T.CIVILIAN, T.CIVILIAN, T.CIVILIAN, T.CULPRIT],
                    [T.CIVILIAN, T.ALIBI, T.TRADE, T.DOG],
                ),
            )
            game.start()
            return self._dealt(game)

        self.assertEqual(deal(3), deal(3))

    def test_random_games_never_start_with_detective_only_hand(self):
        import random

        from games.hannin.engine import Game

        for players in range(3, 9):
            for seed in range(150):
                game = Game(
                    [(f"p{i + 1}", f"P{i + 1}") for i in range(players)],
                    rng=random.Random(players * 1000 + seed),
                )
                game.start()
                self._assert_no_detective_only_hand(game)
