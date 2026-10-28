"""共通の例外。

HTTP ステータスのような Web の都合はここでは決めうちにせず、
``status`` として持たせて ``web`` 層が解釈する。
"""

from __future__ import annotations


class PlatformError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class RoomError(PlatformError):
    """ルーム・セッション操作の失敗。"""


class ConfigurationError(PlatformError):
    """ゲーム設定が不正。"""


class InvalidAction(PlatformError):
    """現在の状態では許可されないゲーム操作。"""
