from __future__ import annotations

from collections import Counter

from games.hannin.cards import Card, CardType
from games.hannin.config import RuleConfig
from games.hannin.engine import Game


def deck_from_hands(*hands: list[CardType]) -> list[Card]:
    """プレイヤーごとの手札（各4枚）から、配布順のデッキを作る。"""
    if any(len(hand) != 4 for hand in hands):
        raise ValueError("各手札は4枚にしてください")
    counters: Counter[CardType] = Counter()
    cards: list[Card] = []
    for hand in hands:
        for card_type in hand:
            cards.append(Card(f"{card_type.value}-{counters[card_type]}", card_type))
            counters[card_type] += 1
    return cards


def make_game(*hands: list[CardType], config: RuleConfig | None = None) -> Game:
    players = [(f"p{index + 1}", f"P{index + 1}") for index in range(len(hands))]
    return Game(players, config=config, deck=deck_from_hands(*hands))


def card_of(game: Game, player_index: int, card_type: CardType) -> Card:
    player = game.players[player_index]
    return next(card for card in player.hand if card.type is card_type)


def play(game: Game, player_index: int, card_type: CardType, **kwargs) -> Card:
    card = card_of(game, player_index, card_type)
    player = game.players[player_index]
    game.play_card(player.id, card.id, **kwargs)
    return card


def all_hand_ids(game: Game) -> list[str]:
    return [card.id for player in game.players for card in player.hand]
