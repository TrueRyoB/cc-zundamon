#!/usr/bin/env python3
"""say-summary.py の単体テスト（標準ライブラリのみ・pytest不要）。
実行: python3 tests/test_say_summary.py

主眼はマイク使用中ミュート機能（issue #1）:
  - mic_in_use() が例外を投げず bool を返す（idle 実機では False）
  - main() が「MIC_MUTE かつ mic_in_use()=True」のときワーカーを起動しない
  - mic off / 機能無効 のときはワーカーを起動する
既存の整形ロジック（clean / split_chunks）も軽く回帰確認する。
"""
import importlib.util
import io
import json
import os
import sys
import tempfile

HOOK = os.path.join(os.path.dirname(__file__), "..", "hooks", "say-summary.py")


def load_module():
    spec = importlib.util.spec_from_file_location("say_summary", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakePopen:
    """subprocess.Popen 差し替え用。呼び出しを記録し stdin をダミー化する。"""
    calls = []

    def __init__(self, args, **kwargs):
        FakePopen.calls.append(args)
        self.stdin = io.BytesIO()


def make_transcript(text):
    fd, path = tempfile.mkstemp(suffix=".jsonl", prefix="tr_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n")
        f.write(json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": text}]},
        }) + "\n")
    return path


def run_main(mod, transcript_path):
    """main() を stdin=JSON, argv=フック本体 で実行し、Popen 呼び出し回数を返す。"""
    FakePopen.calls = []
    real_popen = mod.subprocess.Popen
    real_stdin = sys.stdin
    real_argv = sys.argv
    mod.subprocess.Popen = FakePopen
    sys.stdin = io.StringIO(json.dumps({"transcript_path": transcript_path}))
    sys.argv = ["say-summary.py"]
    try:
        mod.main()
    finally:
        mod.subprocess.Popen = real_popen
        sys.stdin = real_stdin
        sys.argv = real_argv
    return len(FakePopen.calls)


def check(name, cond):
    mark = "ok" if cond else "FAIL"
    print(f"[{mark}] {name}")
    if not cond:
        check.failed += 1
check.failed = 0


def main():
    mod = load_module()

    # --- mic_in_use: 例外なく bool を返す（実機 idle では False 期待） ---
    val = mod.mic_in_use()
    check("mic_in_use() returns bool", isinstance(val, bool))
    check("mic_in_use() is False when mic idle", val is False)

    # --- _fourcc: 既知コードの一致 ---
    check("_fourcc('dIn ')", mod._fourcc("dIn ") == 0x64496E20)
    check("_fourcc('gone')", mod._fourcc("gone") == 0x676F6E65)

    # --- clean: <speak> 抽出 ---
    check("clean extracts <speak>",
          mod.clean("前置き\n<speak>要約だけ</speak>") == "要約だけ")

    tr = make_transcript("<speak>マイクテスト読み上げ</speak>")
    try:
        # mic OFF -> 読み上げる（Popen 1回）
        mod.MIC_MUTE = True
        mod.mic_in_use = lambda: False
        check("mic OFF -> worker spawned", run_main(mod, tr) == 1)

        # mic ON + MIC_MUTE -> ミュート（Popen 0回）
        mod.MIC_MUTE = True
        mod.mic_in_use = lambda: True
        check("mic ON + MIC_MUTE -> muted (no worker)", run_main(mod, tr) == 0)

        # mic ON + 機能無効 -> 読み上げる（Popen 1回）
        mod.MIC_MUTE = False
        mod.mic_in_use = lambda: True
        check("mic ON + MIC_MUTE disabled -> worker spawned", run_main(mod, tr) == 1)

        # DISABLE 時は常に無音（Popen 0回）
        mod.MIC_MUTE = True
        mod.mic_in_use = lambda: False
        mod.DISABLE = True
        check("DISABLE -> no worker", run_main(mod, tr) == 0)
    finally:
        os.remove(tr)

    print()
    if check.failed:
        print(f"{check.failed} test(s) FAILED")
        sys.exit(1)
    print("all tests passed")


if __name__ == "__main__":
    main()
