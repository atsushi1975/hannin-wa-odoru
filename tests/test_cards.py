from __future__ import annotations

import unittest
from collections import Counter

from games.hannin.cards import TOTAL_COUNTS, CardType, full_deck


class CardDefinitionTest(unittest.TestCase):
    def test_full_deck_has_32_unique_cards(self):
        deck = full_deck()
        self.assertEqual(len(deck), 32)
        self.assertEqual(len({card.id for card in deck}), 32)
        self.assertEqual(sum(TOTAL_COUNTS.values()), 32)

    def test_known_print_counts(self):
        counts = Counter(card.type for card in full_deck())
        self.assertEqual(counts[CardType.FIRST_FINDER], 1)
        self.assertEqual(counts[CardType.CULPRIT], 1)
        self.assertEqual(counts[CardType.DETECTIVE], 4)
        self.assertEqual(counts[CardType.ALIBI], 5)
        self.assertEqual(counts[CardType.TRICK], 2)
        self.assertEqual(counts[CardType.DOG], 1)
        self.assertEqual(counts[CardType.BOY], 1)
        self.assertEqual(counts[CardType.WITNESS], 3)
        self.assertEqual(counts[CardType.TRADE], 5)
        self.assertEqual(counts[CardType.RUMOR], 4)
        self.assertEqual(counts[CardType.INFO_OP], 3)
        self.assertEqual(counts[CardType.CIVILIAN], 2)
