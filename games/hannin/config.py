"""人数別の山札レシピとルール設定。

ルールの数値・スイッチはここに集約し、engine.py にハードコードしない。
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass

from .cards import TOTAL_COUNTS, Card, CardType

HAND_SIZE = 4

# 少人数でも推理の手がかりがゼロにならないよう、この中のどれか1枚は必ず入れる。
INFO_CARDS: tuple[CardType, ...] = (CardType.DOG, CardType.BOY, CardType.WITNESS)


@dataclass(frozen=True, slots=True)
class DeckRecipe:
    fixed: Mapping[CardType, int]
    filler: int
    # 補充が偏っても、この中のどれか1枚は山札に入れる（既定は空 = 制約なし）。
    guarantee_one_of: tuple[CardType, ...] = ()

    @property
    def total(self) -> int:
        return sum(self.fixed.values()) + self.filler


@dataclass(frozen=True, slots=True)
class RuleConfig:
    min_players: int = 3
    max_players: int = 8
    # 探偵を使えるようになる自分の手番回数（2 なら2周目から）。
    detective_min_turn: int = 2
    # 手札が「探偵」と「犯人」だけなら、2周目前でも探偵を出せるが効果は無効。
    detective_inert_with_only_detective_or_culprit: bool = True
    # 探偵カードが場に出きったら犯人の勝ち。
    # 公式に明記がなく、少人数では犯人が探偵を捨てるだけで勝ててしまうため既定は無効。
    culprit_wins_when_detectives_exhausted: bool = False
    # たくらみを出した人が自分で犯人を当てた場合、その人だけ負け。
    accomplice_catching_culprit_loses: bool = True


# 公式ルールの人数別構成。filler は残りカードからランダムに選ぶ。
# 3〜5人だけ guarantee_one_of で情報カード1枚を確定で混ぜる（体験底上げのハウスルール）。
RECIPES: dict[int, DeckRecipe] = {
    3: DeckRecipe(
        {CardType.FIRST_FINDER: 1, CardType.CULPRIT: 1, CardType.DETECTIVE: 1, CardType.ALIBI: 1},
        8,
        guarantee_one_of=INFO_CARDS,
    ),
    4: DeckRecipe(
        {
            CardType.FIRST_FINDER: 1,
            CardType.CULPRIT: 1,
            CardType.DETECTIVE: 1,
            CardType.ALIBI: 1,
            CardType.TRICK: 1,
        },
        11,
        guarantee_one_of=INFO_CARDS,
    ),
    5: DeckRecipe(
        {
            CardType.FIRST_FINDER: 1,
            CardType.CULPRIT: 1,
            CardType.DETECTIVE: 1,
            CardType.ALIBI: 2,
            CardType.TRICK: 1,
        },
        14,
        guarantee_one_of=INFO_CARDS,
    ),
    6: DeckRecipe(
        {
            CardType.FIRST_FINDER: 1,
            CardType.CULPRIT: 1,
            CardType.DETECTIVE: 2,
            CardType.ALIBI: 2,
            CardType.TRICK: 2,
        },
        16,
    ),
    7: DeckRecipe(
        {
            CardType.FIRST_FINDER: 1,
            CardType.CULPRIT: 1,
            CardType.DETECTIVE: 2,
            CardType.ALIBI: 3,
            CardType.TRICK: 2,
        },
        19,
    ),
    8: DeckRecipe(dict(TOTAL_COUNTS), 0),
}


def recipe_for(players: int) -> DeckRecipe:
    if players not in RECIPES:
        raise ValueError(f"人数は {min(RECIPES)}〜{max(RECIPES)} 人です: {players}")
    return RECIPES[players]


def build_deck(players: int, rng: random.Random | None = None) -> list[Card]:
    """人数別レシピから山札を作る。

    固定カードを先に確保し、残りのプールから filler 枚をランダムに足す。
    ``guarantee_one_of`` があれば、その種類が固定分に無いときに限り
    プールから1枚を確定で混ぜてから補充する。最後に全体をシャッフルする。
    """
    rng = rng or random.Random()
    recipe = recipe_for(players)
    deck: list[Card] = []
    pool: list[Card] = []

    for card_type, total in TOTAL_COUNTS.items():
        cards = [Card(f"{card_type.value}-{i}", card_type) for i in range(total)]
        fixed = recipe.fixed.get(card_type, 0)
        if fixed > total:
            raise ValueError(f"固定枚数が多すぎます: {card_type} ({fixed} > {total})")
        deck.extend(cards[:fixed])
        pool.extend(cards[fixed:])

    if recipe.filler > len(pool):
        raise ValueError(f"補充枚数が多すぎます: {recipe.filler} > {len(pool)}")
    rng.shuffle(pool)

    filler_cards: list[Card] = []
    if recipe.filler > 0 and recipe.guarantee_one_of:
        already = any(card.type in recipe.guarantee_one_of for card in deck)
        candidates = [card for card in pool if card.type in recipe.guarantee_one_of]
        if not already and candidates:
            chosen = candidates[rng.randrange(len(candidates))]
            pool.remove(chosen)
            filler_cards.append(chosen)

    filler_cards.extend(pool[: recipe.filler - len(filler_cards)])
    deck.extend(filler_cards)

    if recipe.total != players * HAND_SIZE:
        raise ValueError(
            f"レシピの枚数が不正です: {players}人 -> {recipe.total}枚 "
            f"(期待値 {players * HAND_SIZE}枚)"
        )
    if len(deck) != recipe.total:
        raise ValueError(f"山札の枚数が不正です: {len(deck)} != {recipe.total}")
    rng.shuffle(deck)
    return deck
