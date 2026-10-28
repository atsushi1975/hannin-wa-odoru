"""カードの定義。

このモジュールは Flask や Web 層に依存させないこと。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CardType(StrEnum):
    FIRST_FINDER = "first_finder"
    CULPRIT = "culprit"
    DETECTIVE = "detective"
    ALIBI = "alibi"
    TRICK = "trick"
    DOG = "dog"
    BOY = "boy"
    WITNESS = "witness"
    TRADE = "trade"
    RUMOR = "rumor"
    INFO_OP = "info_op"
    CIVILIAN = "civilian"


LABELS: dict[CardType, str] = {
    CardType.FIRST_FINDER: "第一発見者",
    CardType.CULPRIT: "犯人",
    CardType.DETECTIVE: "探偵",
    CardType.ALIBI: "アリバイ",
    CardType.TRICK: "たくらみ",
    CardType.DOG: "いぬ",
    CardType.BOY: "少年",
    CardType.WITNESS: "目撃者",
    CardType.TRADE: "取り引き",
    CardType.RUMOR: "うわさ",
    CardType.INFO_OP: "情報操作",
    CardType.CIVILIAN: "一般人",
}

DESCRIPTIONS: dict[CardType, str] = {
    CardType.FIRST_FINDER: "事件を宣言してゲームを始める。",
    CardType.CULPRIT: "最後の1枚なら出して勝利。",
    CardType.DETECTIVE: "1人を指名し、犯人就判定できれば勝利。2周目から。",
    CardType.ALIBI: "持っていれば犯人でも「違う」と答えられる。",
    CardType.TRICK: "犯人の味方。犯人の勝敗を共有する。",
    CardType.DOG: "1人の手札を1枚暴き、犯人なら勝利。",
    CardType.BOY: "犯人の居場所を自分だけ確認する。",
    CardType.WITNESS: "1人の手札を自分だけ確認する。",
    CardType.TRADE: "1人と手札を1枚ずつ交換する。",
    CardType.RUMOR: "全員が右隣から1枚引く。",
    CardType.INFO_OP: "全員が左隣へ1枚渡す。",
    CardType.CIVILIAN: "効果なし。",
}

# 公式の32枚構成（第三版）。
TOTAL_COUNTS: dict[CardType, int] = {
    CardType.FIRST_FINDER: 1,
    CardType.CULPRIT: 1,
    CardType.DETECTIVE: 4,
    CardType.ALIBI: 5,
    CardType.TRICK: 2,
    CardType.DOG: 1,
    CardType.BOY: 1,
    CardType.WITNESS: 3,
    CardType.TRADE: 5,
    CardType.RUMOR: 4,
    CardType.INFO_OP: 3,
    CardType.CIVILIAN: 2,
}


@dataclass(frozen=True, slots=True)
class Card:
    """1枚のカード。

    ``id`` は同一種類でも一意。カードの移動は必ず id で追跡する。
    """

    id: str
    type: CardType

    @property
    def label(self) -> str:
        return LABELS[self.type]

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "type": self.type.value,
            "label": self.label,
            "description": DESCRIPTIONS[self.type],
        }


def full_deck() -> list[Card]:
    """公式の32枚を、種類ごとに連番 id を振って返す。"""
    return [
        Card(f"{card_type.value}-{index}", card_type)
        for card_type in CardType
        for index in range(TOTAL_COUNTS[card_type])
    ]
