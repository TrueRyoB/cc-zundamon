#!/usr/bin/env python3
"""Stop hook: Claudeの最後の返答を音声で読み上げる（ずんだもん）。
- トークンは一切使わない（生成済みテキストの後処理のみ）
- 全文棒読みはしない:
    1) <speak>...</speak> があればその中身だけを読む
    2) 無ければ コードブロック/表/URL/装飾を削り、先頭からMAX_CHARSまでに切って読む
- 音声: ローカルの VOICEVOX engine (本物のずんだもん) を優先。
        engineが落ちている等、本当に合成できないときだけ macOS `say` にフォールバック。
- 長文対策: VOICEVOX synthesis は概ね ~0.1秒/文字と遅く、長文を1回で合成すると
        synthesis のタイムアウトを超えて say(Kyoko) に落ちてしまう。これを避けるため
        文単位にチャンク分割して各合成を小さく速く保つ。
        ただし再生は「全チャンクを合成しきってから1本に結合し、間を挟まず一気に流す」。
        （旧: 合成→再生→合成 の直列だと再生の合間に次の合成待ちが挟まり声が途切れていた。
          トレードオフとして最初の音が出るまでの待ちは全文合成完了まで延びる。）
        さらに音声処理はワーカープロセスに切り離し、Stopフック本体は即座に返す
        （プロンプトが合成完了までブロックされない）。
環境変数で調整可:
    ZUNDA_URL     (既定: http://localhost:50021)
    ZUNDA_SPEAKER (既定: 3  = ずんだもん/ノーマル)
    ZUNDA_MAX     (既定: 220   = <speak>無し時の総文字数上限)
    ZUNDA_SPEAK_MAX (既定: 1200 = <speak>有り時の総文字数上限。暴走読み上げ防止)
    ZUNDA_CHUNK   (既定: 100   = 1合成あたりの目安文字数)
    ZUNDA_SPEED   (既定: 1.2   = 再生速度。VOICEVOXは音程を変えず話速のみ変更)
    ZUNDA_SAY_VOICE (フォールバック用, 既定: Kyoko)
    ZUNDA_SAY_RATE  (フォールバック用, 既定: 210。実際の rate は ×ZUNDA_SPEED)
    ZUNDA_DISABLE   (立てるとナレーター無効。ヘッドレス/バッチ実行時に使う)
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import wave

URL = os.environ.get("ZUNDA_URL", "http://localhost:50021")
SPEAKER = os.environ.get("ZUNDA_SPEAKER", "3")
MAX_CHARS = int(os.environ.get("ZUNDA_MAX", "220"))
SPEAK_MAX = int(os.environ.get("ZUNDA_SPEAK_MAX", "1200"))
CHUNK_CHARS = int(os.environ.get("ZUNDA_CHUNK", "100"))
SAY_VOICE = os.environ.get("ZUNDA_SAY_VOICE", "Kyoko")
SAY_RATE = os.environ.get("ZUNDA_SAY_RATE", "210")
# 再生速度。VOICEVOX は speedScale で音程を変えず話速のみ上げる。say フォールバックは rate に乗算。
SPEED = float(os.environ.get("ZUNDA_SPEED", "1.2"))
# ナレーター無効化スイッチ。ヘッドレス/バッチ実行では ZUNDA_DISABLE=1 を立てて黙らせる。
DISABLE = os.environ.get("ZUNDA_DISABLE", "").strip().lower() not in ("", "0", "false", "no", "off")


def last_assistant_text(transcript_path):
    text = None
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") != "assistant":
                    continue
                content = ev.get("message", {}).get("content", [])
                parts = [
                    c.get("text", "")
                    for c in content
                    if isinstance(c, dict) and c.get("type") == "text"
                ]
                joined = "".join(parts).strip()
                if joined:
                    text = joined  # 最後のものを残す
    except FileNotFoundError:
        return None
    return text


def clean(text):
    # <speak>...</speak> があれば最優先でその中身だけ
    m = re.findall(r"<speak>(.*?)</speak>", text, re.DOTALL)
    if m:
        spoken = re.sub(r"\s+", " ", m[-1]).strip()
        # 暴走読み上げ防止に総量だけ上限を設ける（チャンク分割は別途）
        if len(spoken) > SPEAK_MAX:
            spoken = spoken[:SPEAK_MAX] + " 以下略"
        return spoken
    # コードブロックを丸ごと除去
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    # 表の行を除去
    text = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("|"))
    # URL除去
    text = re.sub(r"https?://\S+", "", text)
    # インラインコードのバッククォート・装飾記号を除去
    text = text.replace("`", "")
    text = re.sub(r"[*_#>~\-]{1,}", " ", text)
    # 空白畳み込み
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + " 以下略"
    return text


def split_chunks(text, limit):
    """文の区切り（。．.!?！？、）を保ったまま、limit 文字程度のチャンクに詰める。"""
    # 区切り文字の直後で分割（区切り文字はチャンク側に残す）
    sentences = re.split(r"(?<=[。．.!?！？、])", text)
    chunks = []
    buf = ""
    for s in sentences:
        if not s:
            continue
        # 1文が単体で limit を超える場合は強制的に切る
        while len(s) > limit:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(s[:limit])
            s = s[limit:]
        if len(buf) + len(s) > limit and buf:
            chunks.append(buf)
            buf = s
        else:
            buf += s
    if buf:
        chunks.append(buf)
    return chunks


def synth_voicevox(text):
    """VOICEVOXで合成。成功したら wavパスを返す。失敗時 None。"""
    try:
        q = urllib.parse.urlencode({"speaker": SPEAKER, "text": text})
        req = urllib.request.Request(f"{URL}/audio_query?{q}", method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            query = r.read()
        # 話速を適用（音程は変えずスピードのみ）。失敗しても等倍で続行。
        if SPEED != 1.0:
            try:
                qd = json.loads(query)
                qd["speedScale"] = SPEED
                query = json.dumps(qd).encode("utf-8")
            except Exception:
                pass
        req2 = urllib.request.Request(
            f"{URL}/synthesis?speaker={SPEAKER}",
            data=query,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req2, timeout=60) as r:
            wav = r.read()
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="zunda_")
        with os.fdopen(fd, "wb") as f:
            f.write(wav)
        return path
    except Exception:
        return None


def concat_wavs(paths):
    """複数の wav を1本に結合して結合ファイルのパスを返す。失敗時 None。
    VOICEVOX 出力は全チャンク同一フォーマットなので frames の連結だけで繋がる。"""
    try:
        fd, out = tempfile.mkstemp(suffix=".wav", prefix="zunda_all_")
        os.close(fd)
        with wave.open(paths[0], "rb") as w0:
            params = w0.getparams()
        with wave.open(out, "wb") as wout:
            wout.setparams(params)
            for p in paths:
                with wave.open(p, "rb") as w:
                    wout.writeframes(w.readframes(w.getnframes()))
        return out
    except Exception:
        return None


def worker(spoken):
    """別プロセスで実行される読み上げ本体。
    全チャンクを先に合成しきってから1本に結合し、間を挟まず一気に再生する
    （チャンク境界での合成待ちによる「途切れ」を無くすため）。"""
    # 前の読み上げが残っていたら止める
    subprocess.run(["pkill", "-x", "afplay"], capture_output=True)
    subprocess.run(["pkill", "-x", "say"], capture_output=True)

    chunks = [c.strip() for c in split_chunks(spoken, CHUNK_CHARS) if c.strip()]
    if not chunks:
        return

    # 1) まず全チャンクを合成しきる（1つでも失敗したら engine 不調と見なす）
    wavs = []
    ok = True
    for chunk in chunks:
        wav = synth_voicevox(chunk)
        if wav:
            wavs.append(wav)
        else:
            ok = False
            break

    if ok and wavs:
        # 2) 揃ったら結合して1本にし、無音の隙間なく再生
        combined = concat_wavs(wavs)
        try:
            if combined:
                subprocess.run(["afplay", combined])
            else:
                # 結合に失敗した時だけ順次再生（隙間は出るが保険）
                for w in wavs:
                    subprocess.run(["afplay", w])
        finally:
            for w in wavs + ([combined] if combined else []):
                try:
                    os.remove(w)
                except OSError:
                    pass
    else:
        # VOICEVOX が使えない → say で全文を一括読み（say は長文でも途切れない）
        for w in wavs:
            try:
                os.remove(w)
            except OSError:
                pass
        say_rate = str(int(float(SAY_RATE) * SPEED))
        subprocess.run(["say", "-v", SAY_VOICE, "-r", say_rate, spoken])


def main():
    # ナレーター無効化時（例: ヘッドレス/バッチ実行）は何もせず即座に返す
    if DISABLE:
        return
    # ワーカーとして呼ばれた場合: stdin から読み上げテキストを受け取り再生する
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        spoken = sys.stdin.read()
        if spoken.strip():
            worker(spoken)
        return

    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return
    path = data.get("transcript_path")
    if not path:
        return
    raw = last_assistant_text(path)
    if not raw:
        return
    spoken = clean(raw)
    if not spoken:
        return

    # 音声処理はワーカーに切り離し、フック本体は即座に返す
    # （長文合成の完了をここで待たない＝プロンプトをブロックしない）
    p = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "--worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # 親から切り離す
    )
    try:
        p.stdin.write(spoken.encode("utf-8"))
        p.stdin.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
