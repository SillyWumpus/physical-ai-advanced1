#!/usr/bin/env python3
"""ステップ3: 日本語の指示 → Gemini → ドローン操作(関数呼び出し)

準備:
    pip install -U google-genai
    export GEMINI_API_KEY="あなたのキー"     # チャットには貼らないこと
実行:
    python3 drone_llm.py --dry-run   # シミュレータなしで、LLMが何を呼ぶか確認
    python3 drone_llm.py             # 実際にドローンを動かす(要シミュレータ起動)
drone_ctl.py と同じフォルダに置いてください。
"""
import os
import sys

from google import genai
from google.genai import types

# モデルが廃止・変更されたら、環境変数 GEMINI_MODEL で差し替え可能
MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")

SPEED = 0.5          # m/s  (距離 → 時間への換算に使用。シミュレータ上では誤差あり)
YAW_RATE = 0.5       # rad/s
MAX_DISTANCE = 5.0   # 1回の移動の上限 [m]
MAX_ANGLE = 180.0    # 1回の回転の上限 [度]
MAX_HOVER = 10.0     # 秒

DRY_RUN = "--dry-run" in sys.argv


class FakeDrone:
    """--dry-run 用。ROSを使わず、呼び出し内容を表示するだけ。"""

    def takeoff(self):
        print("   [DRY] takeoff")

    def land(self):
        print("   [DRY] land")

    def move(self, vx=0.0, vy=0.0, vz=0.0, yaw_rate=0.0, duration=1.0):
        print(f"   [DRY] move vx={vx} vy={vy} vz={vz} yaw={yaw_rate} t={duration:.1f}s")

    def hover(self):
        pass

    def shutdown(self):
        pass


if DRY_RUN:
    drone = FakeDrone()
else:
    from drone_ctl import DroneController

    drone = DroneController()

state = {"flying": False}


# ---- LLMに見せるツール(関数)。型ヒントとdocstringがそのままLLMへの説明になる ----
def takeoff() -> str:
    """地上のドローンを離陸させる。最初に1回だけ呼ぶ。"""
    if state["flying"]:
        return "エラー: すでに飛行中です。"
    drone.takeoff()
    state["flying"] = True
    return "離陸しました。"


def land() -> str:
    """ドローンを着陸させる。"""
    if not state["flying"]:
        return "エラー: すでに地上にいます。"
    drone.land()
    state["flying"] = False
    return "着陸しました。"


def _check(distance: float) -> str | None:
    if not state["flying"]:
        return "エラー: 地上にいます。先にtakeoffしてください。"
    if abs(distance) > MAX_DISTANCE:
        return f"エラー: 1回の移動は{MAX_DISTANCE}m以内にしてください。"
    return None


def move_forward(distance_m: float) -> str:
    """前方へ移動する。distance_mが負なら後退。単位はメートル(最大5)。"""
    if err := _check(distance_m):
        return err
    drone.move(vx=SPEED if distance_m >= 0 else -SPEED, duration=abs(distance_m) / SPEED)
    return f"前進 {distance_m} m 完了。"


def move_left(distance_m: float) -> str:
    """左へ移動する。distance_mが負なら右へ。単位はメートル(最大5)。"""
    if err := _check(distance_m):
        return err
    drone.move(vy=SPEED if distance_m >= 0 else -SPEED, duration=abs(distance_m) / SPEED)
    return f"左移動 {distance_m} m 完了。"


def move_up(distance_m: float) -> str:
    """上昇する。distance_mが負なら下降。単位はメートル(最大5)。"""
    if err := _check(distance_m):
        return err
    drone.move(vz=SPEED if distance_m >= 0 else -SPEED, duration=abs(distance_m) / SPEED)
    return f"上昇 {distance_m} m 完了。"


def rotate(degrees: float) -> str:
    """その場で回転する。正の値は左回り(反時計回り)、負の値は右回り。単位は度(最大180)。"""
    if not state["flying"]:
        return "エラー: 地上にいます。先にtakeoffしてください。"
    if abs(degrees) > MAX_ANGLE:
        return f"エラー: 1回の回転は{MAX_ANGLE}度以内にしてください。"
    rad = abs(degrees) * 3.14159265 / 180.0
    drone.move(yaw_rate=YAW_RATE if degrees >= 0 else -YAW_RATE, duration=rad / YAW_RATE)
    return f"回転 {degrees} 度 完了。"


def hover(seconds: float) -> str:
    """その場で停止(ホバリング)して待つ。単位は秒(最大10)。"""
    import time

    if not state["flying"]:
        return "エラー: 地上にいます。"
    drone.hover()
    time.sleep(min(max(seconds, 0.0), MAX_HOVER))
    return f"{seconds}秒 ホバリングしました。"


SYSTEM = """あなたはシミュレータ上の小型ドローンを操作するアシスタントです。
ユーザーの日本語の指示を、与えられた関数の呼び出しに変換して順番に実行してください。
- ドローンは最初、地上にいます。飛ぶ前に takeoff が必要です。
- 前後左右上下の指示は、向いている方向(ドローン基準)で解釈します。
- 距離や角度が指定されていない場合は、1m、90度のように常識的な値を使い、その値を最後に伝えてください。
- 関数がエラーを返したら、勝手にやり直さず、理由をユーザーに伝えてください。
- 関数で実現できない指示(撮影、高速飛行、危険な操作など)は断ってください。
- 返答は短く、日本語で。"""


def main():
    client = genai.Client()  # 環境変数 GEMINI_API_KEY (または GOOGLE_API_KEY) を読む
    chat = client.chats.create(
        model=MODEL,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM,
            tools=[takeoff, land, move_forward, move_left, move_up, rotate, hover],
            temperature=0,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                maximum_remote_calls=30
            ),
        ),
    )
    print(f"モデル: {MODEL} / {'DRY-RUN' if DRY_RUN else '実機(シミュレータ)'}")
    print("日本語で指示を入力(例: 離陸して2m前進して右に90度回って着陸)。q で終了。")
    try:
        while True:
            text = input("指示> ").strip()
            if text in ("q", "quit", "exit"):
                break
            if not text:
                continue
            try:
                res = chat.send_message(text)
                print(res.text or "(完了)")
            except Exception as e:  # APIエラー(モデル名違い、上限超過など)
                print("エラー:", e)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        if state["flying"]:
            print("終了前に着陸します…")
            drone.land()
        drone.shutdown()


if __name__ == "__main__":
    main()
