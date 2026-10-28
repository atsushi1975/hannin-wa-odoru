"""ゲームの登録場所。

ここで import したモジュールが ``gameplatform.games`` に自分を登録する。
新しいゲームを足すときは、このファイルに 1 行追加する。
"""

from __future__ import annotations

from . import hannin  # noqa: F401  （登録のための副作用 import）
