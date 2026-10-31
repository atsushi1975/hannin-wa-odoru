"""単独ヘルスチェック用 Flask アプリ。

``GET /health`` -> ``{"title": ..., "version": ..., "status": ...}``（共通仕様）。

catan と同じ「共通ヘルスチェック」を単独の Flask アプリとして切り出して再利用するための
アプリ。ゲーム固有のロジックは持たず、status は ``gameplatform`` 以下のゲーム非依存の
状態ソースから組み立てる（``catan`` では自前のルーム状態を渡すことになる）。

Run:
    python health_app.py
"""

from __future__ import annotations

from flask import Flask, jsonify

from gameplatform import rooms
from web.api import _health_version, health_status


def create_health_app(title: str, get_status) -> Flask:
    """title と status 取得関数から、共通ヘルスチェックの Flask アプリを作る。"""
    app = Flask(__name__)

    @app.get("/health")
    def health():
        status = "idle"
        try:
            status = get_status() or "idle"
        except Exception:
            status = "idle"  # 死活確認は例外で 500 にせず、必ず JSON を返す
        return jsonify({"title": title, "version": _health_version(), "status": status})

    return app


def hannin_status() -> str:
    """犯人は踊るのルーム状態から status を組み立てる。"""
    return health_status()


app = create_health_app("犯人は踊る", hannin_status)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
