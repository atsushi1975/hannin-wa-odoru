from __future__ import annotations

import copy
import logging
import random
import unittest
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from games.hannin import CpuDriver, act
from games.hannin.config import RuleConfig
from games.hannin.cpu import DEFAULT_MEMORY_WINDOW, DETECTIVE_THRESHOLD, CulpritEstimator, choose_action
from games.hannin.engine import FINISHED, PENDING, TURN, Game, InvalidAction
from games.hannin.views import player_view

IDS = ("p1", "p2", "p3", "p4")


def card(card_type: str, index: int = 9) -> dict[str, str]:
    return {"id": f"{card_type}-{index}", "type": card_type, "label": card_type, "description": ""}


def event(seq: int, kind: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"seq": seq, "kind": kind, "message": kind}
    if data is not None:
        result["data"] = data
    return result


def note(seq: int, kind: str, **extra: Any) -> dict[str, Any]:
    return {"seq": seq, "kind": kind, "message": kind, **extra}


def counts(*values: int, ids: tuple[str, ...] = IDS) -> dict[str, Any]:
    return {"handCounts": dict(zip(ids, values))}


def make_view(
    *,
    ids: tuple[str, ...] = IDS,
    me: str = "p1",
    started_by: str = "p2",
    hand: tuple[str, ...] = ("civilian", "civilian", "civilian"),
    events: list[dict[str, Any]] | None = None,
    notes: list[dict[str, Any]] | None = None,
    played: list[dict[str, Any]] | None = None,
    hand_counts: dict[str, int] | None = None,
    turns_taken: int = 2,
    **extra: Any,
) -> dict[str, Any]:
    events = events if events is not None else [event(1, "start")]
    hand_counts = hand_counts or {}
    view = {
        "revision": len(events),
        "phase": "turn",
        "currentPlayerId": me,
        "startedBy": started_by,
        "players": [
            {
                "id": player_id,
                "name": player_id.upper(),
                "handCount": hand_counts.get(player_id, 3 if player_id == started_by else 4),
                "turnsTaken": turns_taken if player_id == me else 0,
                "tricksPlayed": 0,
                "isCurrent": player_id == me,
                "isMe": player_id == me,
            }
            for player_id in ids
        ],
        "me": {
            "id": me,
            "hand": [card(card_type, index) for index, card_type in enumerate(hand)],
            "notes": notes or [],
        },
        "played": played or [],
        "pending": None,
        "events": events,
        "result": None,
    }
    view.update(extra)
    return view


def started(estimator: CulpritEstimator, **kwargs: Any) -> CulpritEstimator:
    estimator.update(make_view(**kwargs))
    return estimator


def values(estimator: CulpritEstimator, ids: tuple[str, ...] = IDS) -> list[float]:
    probabilities = estimator.probabilities()
    return [probabilities[player_id] for player_id in ids]


class EstimatorTestCase(unittest.TestCase):
    def assertProbabilities(self, estimator: CulpritEstimator, expected: list[float]) -> None:
        actual = values(estimator)
        for got, want in zip(actual, expected):
            self.assertAlmostEqual(got, want, places=9, msg=f"{actual} != {expected}")
        self.assertAlmostEqual(sum(actual), 1.0, places=9)


class InitialTest(EstimatorTestCase):
    def test_holding_culprit(self):
        estimator = started(CulpritEstimator("p1"), hand=("culprit", "civilian", "civilian", "civilian"))
        self.assertProbabilities(estimator, [1, 0, 0, 0])

    def test_not_holding_is_proportional_to_hand_size(self):
        # 第一発見者 p2 は HAND_SIZE-1 = 3 枚、他は 4 枚。
        estimator = started(CulpritEstimator("p1"))
        self.assertProbabilities(estimator, [0, 3 / 11, 4 / 11, 4 / 11])

    def test_first_finder_is_me(self):
        estimator = started(CulpritEstimator("p1"), started_by="p1")
        self.assertProbabilities(estimator, [0, 1 / 3, 1 / 3, 1 / 3])


class EmptyHandTest(EstimatorTestCase):
    def test_player_without_hand_gets_zero_at_start(self):
        # [0, 3/11, 4/11, 4/11] から p3 を0にして割り戻す: p2 = 3/7, p4 = 4/7
        estimator = started(CulpritEstimator("p1"), hand_counts={"p3": 0})
        self.assertProbabilities(estimator, [0, 3 / 7, 0, 4 / 7])

    def test_player_who_plays_last_card_becomes_zero_and_stays_zero(self):
        estimator = started(CulpritEstimator("p1"))
        estimator.update(make_view(events=[event(1, "start"), event(2, "play")], hand_counts={"p4": 0}))
        self.assertProbabilities(estimator, [0, 3 / 7, 4 / 7, 0])
        # 手札0の間は、うわさ等で他の人の持ち分が流れ込んでも p4 は0のまま。
        estimator.update(
            make_view(
                events=[event(1, "start"), event(2, "play"), event(3, "rumor", counts(3, 3, 4, 0))],
                notes=[],
                hand_counts={"p4": 0},
            )
        )
        self.assertEqual(estimator.probabilities()["p4"], 0.0)
        self.assertAlmostEqual(sum(values(estimator)), 1.0, places=9)

    def test_holder_probability_is_untouched(self):
        estimator = started(CulpritEstimator("p1"), hand=("culprit",), hand_counts={"p3": 0})
        self.assertProbabilities(estimator, [1, 0, 0, 0])


class MoveTest(EstimatorTestCase):
    def setUp(self):
        self.estimator = started(CulpritEstimator("p1"))
        self.start = event(1, "start")

    def test_rumor(self):
        # p4 から非犯人を引いた(p4->p1 の移動は無かったことになる)。
        # p2: 3/11 - (3/11)/3 = 2/11, p3: 4/11 - 1/11 + 1/11 = 4/11, p4: 4/11 + 1/11 = 5/11
        view = make_view(
            events=[self.start, event(2, "rumor", counts(3, 3, 4, 4))],
            notes=[note(1, "rumor_draw", player_id="p4", card=card("civilian"))],
        )
        self.estimator.update(view)
        self.assertProbabilities(self.estimator, [0, 2 / 11, 4 / 11, 5 / 11])

    def test_info_op(self):
        # h = (p1:3, p2:2, p3:4, p4:3)
        # p2->p3: (3/11)/2 = 3/22, p3->p4: (4/11)/4 = 2/22, p4->p1 は取り消し
        # p2 = 6/22 - 3/22, p3 = 8/22 - 2/22 + 3/22, p4 = 8/22 + 2/22
        view = make_view(
            events=[self.start, event(2, "info_op", counts(3, 2, 4, 3))],
            notes=[
                note(1, "info_op_sent", player_id="p2", card=card("civilian")),
                note(2, "info_op_received", player_id="p4", card=card("civilian")),
            ],
        )
        self.estimator.update(view)
        self.assertProbabilities(self.estimator, [0, 3 / 22, 9 / 22, 10 / 22])

    def test_trade_between_others(self):
        # p3<->p4, h = (4, 3, 2, 4)。p3->p4: (4/11)/2 = 2/11, p4->p3: (4/11)/4 = 1/11
        view = make_view(events=[self.start, event(2, "trade", {"actorId": "p3", "targetId": "p4", **counts(4, 3, 2, 4)})])
        self.estimator.update(view)
        self.assertProbabilities(self.estimator, [0, 3 / 11, 3 / 11, 5 / 11])

    def test_zero_hand_player_passes_nothing(self):
        # p3 は手札0。p3 から出る分はなく、p2->p3 は流れ込む。
        view = make_view(
            events=[self.start, event(2, "info_op", counts(3, 3, 0, 4))],
            notes=[note(1, "info_op_received", player_id="p4", card=card("civilian"))],
        )
        self.estimator.update(view)
        # p2->p3: 1/11。p4->p1 は取り消し。p3->p4 は無し。
        self.assertProbabilities(self.estimator, [0, 2 / 11, 5 / 11, 4 / 11])


class SelfInvolvedTest(EstimatorTestCase):
    def test_receive_culprit_in_info_op(self):
        estimator = started(CulpritEstimator("p1"))
        view = make_view(
            hand=("civilian", "civilian", "culprit"),
            events=[event(1, "start"), event(2, "info_op", counts(3, 3, 4, 4))],
            notes=[
                note(1, "info_op_sent", player_id="p2", card=card("civilian")),
                note(2, "info_op_received", player_id="p4", card=card("culprit")),
            ],
        )
        estimator.update(view)
        self.assertProbabilities(estimator, [1, 0, 0, 0])

    def test_receive_culprit_by_rumor_and_trade(self):
        for kind, data, notes in (
            ("rumor", counts(3, 3, 4, 4), [note(1, "rumor_draw", player_id="p4", card=card("culprit"))]),
            (
                "trade",
                {"actorId": "p3", "targetId": "p1", **counts(3, 3, 4, 4)},
                [note(1, "trade", player_id="p3", card=card("culprit"))],
            ),
        ):
            with self.subTest(kind=kind):
                estimator = started(CulpritEstimator("p1"))
                estimator.update(
                    make_view(
                        hand=("civilian", "civilian", "culprit"),
                        events=[event(1, "start"), event(2, kind, data)],
                        notes=notes,
                    )
                )
                self.assertProbabilities(estimator, [1, 0, 0, 0])

    def test_pass_culprit(self):
        # (種類, イベントdata, 自分の note, 渡した先)
        cases = (
            ("info_op", counts(4, 3, 4, 4), [note(1, "info_op_sent", player_id="p2", card=card("culprit"))], "p2"),
            ("rumor", counts(4, 3, 4, 4), [], "p2"),  # p2 が自分から引く
            ("trade", {"actorId": "p3", "targetId": "p1", **counts(4, 3, 4, 4)}, [note(1, "trade", player_id="p3", card=card("civilian"))], "p3"),
        )
        for kind, data, notes, destination in cases:
            with self.subTest(kind=kind):
                estimator = started(CulpritEstimator("p1"), hand=("culprit", "civilian", "civilian", "civilian"))
                self.assertProbabilities(estimator, [1, 0, 0, 0])
                estimator.update(
                    make_view(
                        hand=("civilian", "civilian", "civilian", "civilian"),
                        events=[event(1, "start"), event(2, kind, data)],
                        notes=notes,
                    )
                )
                self.assertProbabilities(estimator, [1.0 if p == destination else 0.0 for p in IDS])

    def test_keep_culprit_when_not_passed(self):
        estimator = started(CulpritEstimator("p1"), hand=("culprit", "civilian", "civilian", "civilian"))
        estimator.update(
            make_view(
                hand=("culprit", "civilian", "civilian", "civilian"),
                events=[event(1, "start"), event(2, "info_op", counts(4, 3, 4, 4))],
                notes=[
                    note(1, "info_op_sent", player_id="p2", card=card("civilian")),
                    note(2, "info_op_received", player_id="p4", card=card("civilian")),
                ],
            )
        )
        self.assertProbabilities(estimator, [1, 0, 0, 0])

    def test_receive_non_culprit_in_trade_keeps_probabilities(self):
        estimator = started(CulpritEstimator("p1"))
        estimator.update(
            make_view(
                events=[event(1, "start"), event(2, "trade", {"actorId": "p1", "targetId": "p3", **counts(4, 3, 4, 4)})],
                notes=[note(1, "trade", player_id="p3", card=card("civilian"))],
            )
        )
        self.assertProbabilities(estimator, [0, 3 / 11, 4 / 11, 4 / 11])


class ResultNoteTest(EstimatorTestCase):
    def setUp(self):
        self.estimator = started(CulpritEstimator("p1"))
        self.start = event(1, "start")

    def test_boy_with_player(self):
        self.estimator.update(
            make_view(events=[self.start, event(2, "boy")], notes=[note(1, "boy", player_id="p3")])
        )
        self.assertProbabilities(self.estimator, [0, 0, 1, 0])

    def test_boy_without_player_does_nothing(self):
        self.estimator.update(make_view(events=[self.start, event(2, "boy")], notes=[note(1, "boy")]))
        self.assertProbabilities(self.estimator, [0, 3 / 11, 4 / 11, 4 / 11])

    def test_witness_sees_culprit(self):
        self.estimator.update(
            make_view(
                events=[self.start, event(2, "witness")],
                notes=[note(1, "witness", player_id="p3", cards=[card("alibi"), card("culprit")])],
            )
        )
        self.assertProbabilities(self.estimator, [0, 0, 1, 0])

    def test_witness_sees_no_culprit(self):
        # p3 を0にして残りを割り戻す: p2 = 3/7, p4 = 4/7
        self.estimator.update(
            make_view(
                events=[self.start, event(2, "witness")],
                notes=[note(1, "witness", player_id="p3", cards=[card("alibi"), card("civilian")])],
            )
        )
        self.assertProbabilities(self.estimator, [0, 3 / 7, 0, 4 / 7])

    def test_witness_with_uneven_probabilities_still_sums_to_one(self):
        # rumor で [0, 2/11, 4/11, 5/11] にした後、p4 を見て犯人なし。
        # 固定倍率(3/2)ではなく、合計 6/11 で割る: p2 = 1/3, p3 = 2/3
        self.estimator.update(
            make_view(
                events=[self.start, event(2, "rumor", counts(3, 3, 4, 4))],
                notes=[note(1, "rumor_draw", player_id="p4", card=card("civilian"))],
            )
        )
        self.estimator.update(
            make_view(
                events=[self.start, event(2, "rumor", counts(3, 3, 4, 4)), event(3, "witness")],
                notes=[
                    note(1, "rumor_draw", player_id="p4", card=card("civilian")),
                    note(2, "witness", player_id="p4", cards=[card("civilian")]),
                ],
            )
        )
        self.assertProbabilities(self.estimator, [0, 1 / 3, 2 / 3, 0])

    def test_contradiction_falls_back_to_uniform_over_others_with_hand(self):
        self.estimator.update(make_view(events=[self.start, event(2, "boy")], notes=[note(1, "boy", player_id="p3")]))
        with self.assertLogs("hannin.cpu", level="WARNING"):
            self.estimator.update(
                make_view(
                    events=[self.start, event(2, "boy"), event(3, "witness")],
                    notes=[
                        note(1, "boy", player_id="p3"),
                        note(2, "witness", player_id="p3", cards=[card("civilian")]),
                    ],
                    hand_counts={"p4": 0},
                )
            )
        self.assertProbabilities(self.estimator, [0, 0.5, 0.5, 0])


class MiscTest(EstimatorTestCase):
    def test_other_players_trick_is_excluded_but_probabilities_continue(self):
        estimator = started(CulpritEstimator("p1"))
        played = [
            {"playerId": "p3", "playerName": "P3", "card": card("trick")},
            {"playerId": "p1", "playerName": "P1", "card": card("trick")},
        ]
        estimator.update(make_view(played=played))
        self.assertEqual(estimator.trick_player_ids(), frozenset({"p3"}))
        self.assertProbabilities(estimator, [0, 3 / 11, 4 / 11, 4 / 11])

    def test_same_update_twice_does_not_double_apply(self):
        estimator = started(CulpritEstimator("p1"))
        view = make_view(
            events=[event(1, "start"), event(2, "info_op", counts(3, 2, 4, 3))],
            notes=[
                note(1, "info_op_sent", player_id="p2", card=card("civilian")),
                note(2, "info_op_received", player_id="p4", card=card("civilian")),
            ],
        )
        estimator.update(view)
        first = values(estimator)
        estimator.update(view)
        estimator.update(view)
        self.assertEqual(values(estimator), first)

        witness_view = make_view(
            events=view["events"] + [event(3, "witness")],
            notes=view["me"]["notes"] + [note(3, "witness", player_id="p3", cards=[card("civilian")])],
        )
        estimator.update(witness_view)
        after = values(estimator)
        estimator.update(witness_view)
        self.assertEqual(values(estimator), after)

    def test_event_without_data_is_skipped_with_warning(self):
        estimator = started(CulpritEstimator("p1"))
        view = make_view(
            events=[event(1, "start"), event(2, "rumor"), event(3, "trade", {"actorId": "p2"})],
            notes=[note(1, "rumor_draw", player_id="p4", card=card("civilian"))],
        )
        with self.assertLogs("hannin.cpu", level="WARNING") as logs:
            estimator.update(view)
        self.assertEqual(len(logs.records), 2)
        self.assertProbabilities(estimator, [0, 3 / 11, 4 / 11, 4 / 11])
        # 読み飛ばしたイベントを再度警告しない
        with self.assertNoLogs("hannin.cpu", level="WARNING"):
            estimator.update(view)

    def test_playing_my_culprit_ends_game_without_contradiction(self):
        estimator = started(CulpritEstimator("p1"), hand=("culprit",))
        view = make_view(
            hand=(),
            phase="finished",
            events=[event(1, "start"), event(2, "play"), event(3, "result")],
            played=[{"playerId": "p1", "playerName": "P1", "card": card("culprit")}],
        )
        with self.assertNoLogs("hannin.cpu", level="WARNING"):
            estimator.update(view)
        self.assertProbabilities(estimator, [1, 0, 0, 0])

    def test_debug_log_has_player_names(self):
        estimator = CulpritEstimator("p1")
        with self.assertLogs("hannin.cpu", level=logging.DEBUG) as logs:
            estimator.update(make_view())
        self.assertIn("P2", logs.output[0])

    def test_best_and_worst_target_with_ties(self):
        estimator = started(CulpritEstimator("p1"))  # [0, 3/11, 4/11, 4/11]
        rng = random.Random(1)
        self.assertEqual(estimator.worst_target({"p1"}, rng), "p2")
        self.assertEqual(estimator.worst_target(set(), rng), "p1")
        seen = {estimator.best_target({"p1"}, rng) for _ in range(50)}
        self.assertEqual(seen, {"p3", "p4"})
        self.assertEqual(estimator.best_target({"p1", "p3"}, rng), "p4")
        self.assertIsNone(estimator.best_target(set(IDS), rng))


def civilian_play(index: int) -> dict[str, Any]:
    return {"playerId": "p2", "playerName": "P2", "card": card("civilian", index)}


class Table:
    """公開情報を1手ずつ積み上げる。1手 = 出札1枚 + event 1件（開始時点で1手済み）。"""

    def __init__(self) -> None:
        self.events = [event(1, "start")]
        self.notes: list[dict[str, Any]] = []
        self.played = [civilian_play(0)]

    def step(
        self,
        kind: str,
        data: dict[str, Any] | None = None,
        notes: tuple[tuple[str, dict[str, Any]], ...] = (),
        **view_kwargs: Any,
    ) -> dict[str, Any]:
        seq = self.events[-1]["seq"] + 1
        self.events.append(event(seq, kind, data))
        self.notes.extend(note(seq, note_kind, **extra) for note_kind, extra in notes)
        self.played.append(civilian_play(len(self.played)))
        return self.view(**view_kwargs)

    def view(self, **view_kwargs: Any) -> dict[str, Any]:
        return make_view(events=list(self.events), notes=list(self.notes), played=list(self.played), **view_kwargs)


class MemoryWindowTest(EstimatorTestCase):
    def test_witness_only_counts_inside_window(self):
        # 2手目の目撃で p3 が犯人カードと判明。N=2 なので、4手目（目撃から N 手後）で窓の外になる。
        table = Table()
        estimator = CulpritEstimator("p1", memory_window=2)
        estimator.update(table.view())
        estimator.update(table.step("witness", notes=(("witness", {"player_id": "p3", "cards": [card("culprit")]}),)))
        self.assertProbabilities(estimator, [0, 0, 1, 0])
        estimator.update(table.step("play"))
        self.assertProbabilities(estimator, [0, 0, 1, 0])
        estimator.update(table.step("play"))
        self.assertProbabilities(estimator, [0, 3 / 11, 4 / 11, 4 / 11])
        estimator.update(table.step("play"))
        self.assertProbabilities(estimator, [0, 3 / 11, 4 / 11, 4 / 11])

    def test_boy_only_counts_inside_window(self):
        table = Table()
        estimator = CulpritEstimator("p1", memory_window=3)
        estimator.update(table.view())
        estimator.update(table.step("boy", notes=(("boy", {"player_id": "p4"}),)))
        self.assertProbabilities(estimator, [0, 0, 0, 1])
        estimator.update(table.step("play"))
        estimator.update(table.step("play"))
        self.assertProbabilities(estimator, [0, 0, 0, 1])
        estimator.update(table.step("play"))
        self.assertProbabilities(estimator, [0, 3 / 11, 4 / 11, 4 / 11])

    def test_move_only_counts_inside_window(self):
        # 窓の中の移動（trade）は効く。窓から外れると、移動前の手札枚数ベースに戻る。
        table = Table()
        estimator = CulpritEstimator("p1", memory_window=2)
        estimator.update(table.view())
        estimator.update(table.step("trade", {"actorId": "p3", "targetId": "p4", **counts(4, 3, 2, 4)}))
        self.assertProbabilities(estimator, [0, 3 / 11, 3 / 11, 5 / 11])
        estimator.update(table.step("play"))
        self.assertProbabilities(estimator, [0, 3 / 11, 3 / 11, 5 / 11])
        estimator.update(table.step("play"))
        self.assertProbabilities(estimator, [0, 3 / 11, 4 / 11, 4 / 11])

    def test_zero_window_uses_only_current_view(self):
        table = Table()
        estimator = CulpritEstimator("p1", memory_window=0)
        estimator.update(table.view())
        self.assertProbabilities(estimator, [0, 3 / 11, 4 / 11, 4 / 11])
        estimator.update(table.step("play", hand=("culprit", "civilian", "civilian")))
        self.assertProbabilities(estimator, [1, 0, 0, 0])
        estimator.update(table.step("play"))
        self.assertProbabilities(estimator, [0, 3 / 11, 4 / 11, 4 / 11])

    def test_same_as_full_history_until_window_fills(self):
        table = Table()
        full = CulpritEstimator("p1")
        windowed = CulpritEstimator("p1", memory_window=10)
        views = [
            table.view(),
            table.step("witness", notes=(("witness", {"player_id": "p3", "cards": [card("alibi")]}),)),
            table.step("trade", {"actorId": "p3", "targetId": "p4", **counts(4, 3, 2, 4)}),
            table.step("boy", notes=(("boy", {"player_id": "p2"}),)),
        ]
        for view in views:
            full.update(view)
            windowed.update(view)
            self.assertEqual(values(windowed), values(full))

    def test_repeated_update_gives_same_result(self):
        table = Table()
        estimator = CulpritEstimator("p1", memory_window=2)
        views = [table.view()]
        views.append(table.step("witness", notes=(("witness", {"player_id": "p3", "cards": [card("culprit")]}),)))
        views.append(table.step("play"))
        views.append(table.step("play"))
        views.append(table.step("play"))
        for view in views:
            estimator.update(view)
            first = values(estimator)
            estimator.update(view)
            estimator.update(view)
            self.assertEqual(values(estimator), first)

    def test_history_stays_within_window(self):
        table = Table()
        estimator = CulpritEstimator("p1", memory_window=3)
        estimator.update(table.view())
        for _ in range(30):
            estimator.update(table.step("play"))
        self.assertLessEqual(len(estimator._history), 4)

    def test_trick_players_come_from_all_played_cards(self):
        table = Table()
        estimator = CulpritEstimator("p1", memory_window=1)
        estimator.update(table.view())
        table.played.append({"playerId": "p3", "playerName": "P3", "card": card("trick")})
        estimator.update(table.view())
        for _ in range(5):
            estimator.update(table.step("play"))
        self.assertEqual(estimator.trick_player_ids(), frozenset({"p3"}))

    def test_negative_window_is_rejected(self):
        with self.assertRaises(ValueError):
            CulpritEstimator("p1", memory_window=-1)

    def test_cpu_driver_passes_default_window(self):
        game = Game([("h", "H"), ("c1", "C1"), ("c2", "C2")], config=RuleConfig(), rng=random.Random(0))
        driver = CpuDriver(game, ["c1", "c2"], {})
        for estimator in driver.estimators.values():
            self.assertEqual(estimator._memory_window, DEFAULT_MEMORY_WINDOW)


TRICK_PLAYED = [{"playerId": "p1", "playerName": "P1", "card": card("trick")}]


def estimator_with(probabilities: dict[str, float], **view_kwargs: Any) -> CulpritEstimator:
    estimator = CulpritEstimator("p1")
    estimator.update(make_view(**view_kwargs))
    estimator._p = [probabilities.get(player_id, 0.0) for player_id in IDS]
    return estimator


def actions(hand: tuple[str, ...], probabilities: dict[str, float], *, seeds: int = 40, **view_kwargs: Any):
    """seed を変えて何度も選ばせ、payload の一覧を返す。"""
    view = make_view(hand=hand, **view_kwargs)
    estimator = estimator_with(probabilities, hand=hand, **view_kwargs)
    return [choose_action(view, estimator, random.Random(seed)) for seed in range(seeds)], view


def card_type_of(view: dict[str, Any], payload: dict[str, Any]) -> str:
    return next(c["type"] for c in view["me"]["hand"] if c["id"] == payload["cardId"])


class DetectiveThresholdTest(unittest.TestCase):
    HAND = ("detective", "civilian", "civilian", "civilian")

    def test_threshold_constant(self):
        self.assertEqual(DETECTIVE_THRESHOLD, 0.72)

    def test_plays_detective_at_threshold(self):
        payloads, view = actions(self.HAND, {"p2": 0.2, "p3": 0.72, "p4": 0.08})
        for payload in payloads:
            self.assertEqual(card_type_of(view, payload), "detective")
            self.assertEqual(payload["targetPlayerId"], "p3")

    def test_does_not_play_detective_below_threshold(self):
        payloads, view = actions(self.HAND, {"p2": 0.2, "p3": 0.71, "p4": 0.09})
        for payload in payloads:
            self.assertEqual(card_type_of(view, payload), "civilian")
            self.assertNotIn("targetPlayerId", payload)

    def test_trickster_is_not_a_detective_candidate(self):
        payloads, view = actions(
            self.HAND,
            {"p2": 0.02, "p3": 0.95, "p4": 0.03},
            played=[{"playerId": "p3", "playerName": "P3", "card": card("trick")}],
        )
        for payload in payloads:
            self.assertEqual(card_type_of(view, payload), "civilian")

    def test_player_without_hand_is_not_a_detective_candidate(self):
        payloads, view = actions(self.HAND, {"p2": 0.02, "p3": 0.95, "p4": 0.03}, hand_counts={"p3": 0})
        for payload in payloads:
            self.assertEqual(card_type_of(view, payload), "civilian")

    def test_only_detective_playable_targets_highest_probability(self):
        payloads, view = actions(("detective",) * 4, {"p2": 0.2, "p3": 0.5, "p4": 0.3})
        for payload in payloads:
            self.assertEqual(card_type_of(view, payload), "detective")
            self.assertEqual(payload["targetPlayerId"], "p3")

    def test_detective_is_locked_before_second_turn(self):
        payloads, view = actions(self.HAND, {"p3": 0.99, "p2": 0.01}, turns_taken=0)
        for payload in payloads:
            self.assertEqual(card_type_of(view, payload), "civilian")

    def test_inert_detective_is_played_without_target(self):
        payloads, view = actions(("detective", "culprit"), {"p3": 0.5, "p2": 0.5}, turns_taken=0)
        for payload in payloads:
            self.assertEqual(card_type_of(view, payload), "detective")
            self.assertNotIn("targetPlayerId", payload)

    def test_inert_rule_follows_config(self):
        view = make_view(hand=("detective", "culprit"), turns_taken=0)
        estimator = estimator_with({"p3": 1.0})
        config = RuleConfig(detective_inert_with_only_detective_or_culprit=False)
        self.assertIsNone(choose_action(view, estimator, random.Random(0), config))


class AfterTrickTest(unittest.TestCase):
    HAND = ("detective", "civilian", "civilian", "civilian")

    def test_never_plays_detective_voluntarily(self):
        payloads, view = actions(self.HAND, {"p2": 0.02, "p3": 0.98}, played=TRICK_PLAYED)
        for payload in payloads:
            self.assertEqual(card_type_of(view, payload), "civilian")

    def test_only_detective_targets_lowest_probability(self):
        probabilities = {"p2": 0.3, "p3": 0.5, "p4": 0.2}
        payloads, view = actions(("detective",) * 4, probabilities, played=TRICK_PLAYED)
        for payload in payloads:
            self.assertEqual(card_type_of(view, payload), "detective")
            self.assertEqual(payload["targetPlayerId"], "p4")

    def test_lowest_probability_ignores_players_without_hand(self):
        probabilities = {"p2": 0.3, "p3": 0.5, "p4": 0.2}
        payloads, _ = actions(("detective",) * 4, probabilities, played=TRICK_PLAYED, hand_counts={"p4": 0})
        for payload in payloads:
            self.assertEqual(payload["targetPlayerId"], "p2")

    def test_dog_targets_lowest_probability(self):
        payloads, view = actions(("dog",), {"p2": 0.3, "p3": 0.5, "p4": 0.2}, played=TRICK_PLAYED)
        for payload in payloads:
            self.assertEqual(card_type_of(view, payload), "dog")
            self.assertEqual(payload["targetPlayerId"], "p4")
            self.assertIsNone(payload["targetCardIndex"])


class PlayRulesTest(unittest.TestCase):
    def test_culprit_only_when_last_card(self):
        payloads, view = actions(("culprit", "civilian"), {"p2": 0.5, "p3": 0.5})
        for payload in payloads:
            self.assertEqual(card_type_of(view, payload), "civilian")
        payloads, view = actions(("culprit",), {"p2": 0.5, "p3": 0.5})
        for payload in payloads:
            self.assertEqual(card_type_of(view, payload), "culprit")
            self.assertNotIn("targetPlayerId", payload)

    def test_first_finder_is_never_played(self):
        payloads, view = actions(("first_finder", "civilian"), {"p2": 1.0})
        self.assertTrue(all(card_type_of(view, payload) == "civilian" for payload in payloads))
        view = make_view(hand=("first_finder",))
        self.assertIsNone(choose_action(view, estimator_with({"p2": 1.0}), random.Random(0)))

    def test_cards_are_chosen_uniformly(self):
        payloads, view = actions(("civilian", "alibi", "rumor", "info_op"), {"p2": 1.0}, seeds=400)
        counts = {t: 0 for t in ("civilian", "alibi", "rumor", "info_op")}
        for payload in payloads:
            counts[card_type_of(view, payload)] += 1
        for count in counts.values():
            self.assertGreater(count, 60)

    def test_witness_and_trade_target_someone_else_with_a_hand(self):
        for card_type in ("witness", "trade"):
            payloads, view = actions((card_type, card_type), {"p2": 1.0}, hand_counts={"p3": 0}, seeds=100)
            targets = {payload["targetPlayerId"] for payload in payloads}
            self.assertEqual(targets, {"p2", "p4"}, card_type)

    def test_dog_target_is_random_among_players_with_a_hand_and_index_is_none(self):
        payloads, _ = actions(("dog", "dog"), {"p2": 1.0}, hand_counts={"p3": 0}, seeds=100)
        self.assertEqual({payload["targetPlayerId"] for payload in payloads}, {"p2", "p4"})
        self.assertTrue(all(payload["targetCardIndex"] is None for payload in payloads))

    def test_dog_is_not_playable_when_nobody_has_a_hand(self):
        payloads, view = actions(
            ("dog", "civilian", "culprit"), {"p1": 1.0}, hand_counts={"p2": 0, "p3": 0, "p4": 0}, seeds=20
        )
        self.assertTrue(all(card_type_of(view, payload) == "civilian" for payload in payloads))

    def test_not_my_turn_or_finished(self):
        estimator = estimator_with({"p2": 1.0})
        self.assertIsNone(choose_action(make_view(currentPlayerId="p2"), estimator, random.Random(0)))
        self.assertIsNone(choose_action(make_view(phase="finished"), estimator, random.Random(0)))

    def test_same_seed_gives_same_action(self):
        view = make_view(hand=("civilian", "alibi", "rumor", "info_op"))
        estimator = estimator_with({"p2": 1.0})
        first = choose_action(view, estimator, random.Random(7))
        self.assertEqual(first, choose_action(view, estimator, random.Random(7)))


class PendingTest(unittest.TestCase):
    def pending(self, **overrides: Any) -> dict[str, Any]:
        base = {
            "kind": "trade",
            "actorId": "p2",
            "targetId": "p1",
            "requiredPlayerIds": ["p1", "p2"],
            "waitingPlayerIds": ["p1", "p2"],
            "isRequired": True,
            "hasSelected": False,
            "myOptions": [card("civilian", 1), card("alibi", 2), card("rumor", 3)],
        }
        base.update(overrides)
        return base

    def test_selects_one_of_my_options(self):
        view = make_view(phase="pending", pending=self.pending())
        estimator = estimator_with({"p2": 1.0})
        chosen = {choose_action(view, estimator, random.Random(seed))["cardId"] for seed in range(60)}
        self.assertEqual(chosen, {"civilian-1", "alibi-2", "rumor-3"})
        payload = choose_action(view, estimator, random.Random(0))
        self.assertEqual(payload["type"], "select_card")

    def test_nothing_to_do_when_not_required_or_already_selected(self):
        estimator = estimator_with({"p2": 1.0})
        for overrides in ({"isRequired": False, "myOptions": []}, {"hasSelected": True, "myOptions": []}):
            view = make_view(phase="pending", pending=self.pending(**overrides))
            self.assertIsNone(choose_action(view, estimator, random.Random(0)))


@dataclass
class SelfPlayStats:
    games: int = 0
    finished: int = 0
    rejected: int = 0  # engine が payload を受理しなかった回数
    stuck: int = 0  # 合法手があるのに CPU が行動を返せなかった / 手数ガードに達した回数
    deadlocks: int = 0  # engine 上も出せる札が1枚も無く、手番が進まなくなった回数（ルール側の詰み）
    sum_violations: int = 0  # 各 update 後に p の合計が 1 でなかった回数
    holder_violations: int = 0  # 実際の保持者の p が 0 以下だった回数
    updates: int = 0
    reasons: Counter = field(default_factory=Counter)


MAX_STEPS = 2000
CONFIGS = (RuleConfig(), RuleConfig(detective_min_turn=3, culprit_wins_when_detectives_exhausted=True))


def has_legal_play(game: Game, player_id: str) -> bool:
    """engine のコピーで全カード・全対象を試し、合法手が1つでもあるか調べる（テスト専用）。"""
    others = [player.id for player in game.players if player.id != player_id]
    for card in game.by_id[player_id].hand:
        for target in [None, *others]:
            trial = copy.deepcopy(game)
            try:
                trial.play_card(player_id, card.id, target_player_id=target)
            except InvalidAction:
                continue
            return True
    return False


def selfplay(
    players: int,
    games: int,
    *,
    seed_base: int = 0,
    config: RuleConfig | None = None,
    memory_window: int | None = None,
) -> SelfPlayStats:
    """全員 CPU で最後まで対戦させ、不変条件の違反を数える。"""
    config = config or RuleConfig()
    stats = SelfPlayStats()
    roster = [(f"p{index + 1}", f"P{index + 1}") for index in range(players)]

    for game_no in range(games):
        seed = seed_base + players * 10_000 + game_no
        game = Game(roster, config=config, rng=random.Random(seed))
        rng = random.Random(seed + 5_000_000)
        game.start()
        stats.games += 1
        estimators = {player_id: CulpritEstimator(player_id, memory_window) for player_id, _ in roster}
        last_revision: dict[str, int] = {}

        def sync() -> None:
            holder = game.culprit_holder()
            for player_id, estimator in estimators.items():
                view = player_view(game, player_id)
                if last_revision.get(player_id) == view["revision"]:
                    continue
                last_revision[player_id] = view["revision"]
                estimator.update(view)
                stats.updates += 1
                probabilities = estimator.probabilities()
                if abs(sum(probabilities.values()) - 1.0) > 1e-9:
                    stats.sum_violations += 1
                if holder is not None and probabilities[holder.id] <= 0:
                    stats.holder_violations += 1

        sync()
        for _ in range(MAX_STEPS):
            if game.phase == FINISHED:
                break
            if game.phase == TURN:
                actor = game.players[game.current_index].id
            else:
                assert game.phase == PENDING and game.pending is not None
                actor = next(pid for pid in game.pending.required if pid not in game.pending.selections)
            payload = choose_action(player_view(game, actor), estimators[actor], rng, config)
            if payload is None:
                if game.phase == TURN and not has_legal_play(game, actor):
                    stats.deadlocks += 1
                else:
                    stats.stuck += 1
                break
            try:
                act(game, actor, payload)
            except InvalidAction:
                stats.rejected += 1
                break
            sync()
        else:
            stats.stuck += 1

        if game.phase == FINISHED:
            stats.finished += 1
            stats.reasons[game.result["reason"]] += 1
    return stats


class SelfPlayTest(unittest.TestCase):
    GAMES = 100

    def check(self, players: int, games: int, config: RuleConfig, memory_window: int | None = None) -> SelfPlayStats:
        # 推定の矛盾（合計0）や読み飛ばしが起きれば warning が出るので、それも許さない。
        with self.assertNoLogs("hannin.cpu", level="WARNING"):
            stats = selfplay(players, games, config=config, memory_window=memory_window)
        self.assertEqual(stats.rejected, 0, f"{players}人: engine に拒否された payload")
        self.assertEqual(stats.stuck, 0, f"{players}人: 合法手があるのに行動できない/手数超過")
        # engine 側の詰み（出せる札が無い手札）は CPU の責任ではないので別に数える。
        self.assertEqual(stats.finished + stats.deadlocks, games, f"{players}人: 完走数")
        self.assertEqual(stats.sum_violations, 0, f"{players}人: 合計が1でない")
        self.assertEqual(stats.holder_violations, 0, f"{players}人: 保持者の p が0以下")
        return stats

    def test_default_rules(self):
        for players in range(3, 9):
            with self.subTest(players=players):
                self.check(players, self.GAMES, CONFIGS[0])

    def test_other_rule_settings(self):
        for players in range(3, 9):
            with self.subTest(players=players):
                self.check(players, 30, CONFIGS[1])

    def test_default_memory_window(self):
        for players in range(3, 9):
            with self.subTest(players=players):
                self.check(players, self.GAMES, CONFIGS[0], memory_window=DEFAULT_MEMORY_WINDOW)

    def test_other_rule_settings_with_memory_window(self):
        for players in range(3, 9):
            with self.subTest(players=players):
                self.check(players, 30, CONFIGS[1], memory_window=DEFAULT_MEMORY_WINDOW)


if __name__ == "__main__":
    unittest.main()
