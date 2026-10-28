"""Flask アプリのファクトリ。

ゲームの登録は ``games`` パッケージの import で行う。
"""

from __future__ import annotations

import os

from flask import Flask, render_template

import games  # noqa: F401  （ゲーム登録のための副作用 import）
from .api import api


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-secret"))
    if test_config:
        app.config.update(test_config)
    app.register_blueprint(api)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/rooms/<room_id>")
    def room_page(room_id: str):
        return render_template("room.html", room_id=room_id)

    return app
