from __future__ import annotations

import random
import unittest

from games.hannin.cards import TOTAL_COUNTS, CardType
from games.hannin.config import HAND_SIZE, RECIPES, build_deck, recipe_for


class RecipeTest(unittest.TestCase):
    def test_every_recipe_matches_players_times_hand_size(self):
        for players, recipe in RECIPES.items():
            with self.subTest(players=players):
                self.assertEqual(recipe.total, players * HAND_SIZE)

    def test_recipes_between_three_and_eight_players(self):
        self.assertEqual(sorted(RECIPES), [3, 4, 5, 6, 7, 8])
        with self.assertRaises(ValueError):
            recipe_for(2)
        with self.assertRaises(ValueError):
            recipe_for(9)

    def test_fixed_cards_are_always_present(self):
        for players, recipe in RECIPES.items():
            with self.subTest(players=players):
                deck = build_deck(players, random.Random(players))
                self.assertEqual(len(deck), players * HAND_SIZE)
                self.assertEqual(len({card.id for card in deck}), len(deck))
                for card_type, count in recipe.fixed.items():
                    self.assertGreaterEqual(
                        sum(card.type is card_type for card in deck),
                        count,
                        f"{players}人: {card_type}",
                    )

    def test_eight_players_uses_every_card(self):
        deck = build_deck(8, random.Random(0))
        self.assertEqual(len(deck), 32)
        for card_type, total in TOTAL_COUNTS.items():
            self.assertEqual(sum(card.type is card_type for card in deck), total)

    def test_same_seed_is_reproducible(self):
        first = [card.id for card in build_deck(5, random.Random(42))]
        second = [card.id for card in build_deck(5, random.Random(42))]
        self.assertEqual(first, second)

    def test_three_players_include_minimum_specials(self):
        deck = build_deck(3, random.Random(1))
        types = [card.type for card in deck]
        self.assertIn(CardType.FIRST_FINDER, types)
        self.assertIn(CardType.CULPRIT, types)
        self.assertIn(CardType.DETECTIVE, types)
        self.assertIn(CardType.ALIBI, types)

    def test_three_to_five_players_always_include_an_info_card(self):
        # 補充が偏っても、目撃者・少年・いぬのどれか1枚は必ず入る。
        info = {CardType.DOG, CardType.BOY, CardType.WITNESS}
        for players in (3, 4, 5):
            for seed in range(200):
                with self.subTest(players=players, seed=seed):
                    deck = build_deck(players, random.Random(seed))
                    self.assertTrue(
                        any(card.type in info for card in deck),
                        f"{players}人: 情報カードが1枚も入っていない",
                    )

    def test_decks_never_exceed_card_totals(self):
        for players in RECIPES:
            for seed in range(50):
                deck = build_deck(players, random.Random(seed))
                for card_type, total in TOTAL_COUNTS.items():
                    self.assertLessEqual(
                        sum(card.type is card_type for card in deck),
                        total,
                        f"{players}人: {card_type}",
                    )
