"""開発サーバーの起動: python app.py"""

from __future__ import annotations

import argparse

from web import create_app

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 5000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="犯人は踊る 開発サーバー")
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"待ち受けポート番号（既定: {DEFAULT_PORT}）",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Flask のデバッグ/自動リロードを有効にする",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = create_app()
    app.run(host=DEFAULT_HOST, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()