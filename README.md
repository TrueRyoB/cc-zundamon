# cc-zundamon

**Claude Code の返答を、ずんだもんの声で読み上げる Stop フック。** (macOS)

ローカルの [VOICEVOX](https://voicevox.hiroshiba.jp/) を使って、Claude Code が応答し終えるたびに要点を音声で読み上げます。**追加のトークンは一切消費しません**（生成済みテキストの後処理だけを行います）。

- 🫛 本物のずんだもんの声（VOICEVOX engine）。engine が無いときは macOS の `say` にフォールバック
- 🗣️ 応答末尾を `<speak>...</speak>` で囲むと、その中身だけを読み上げ（囲まなければ本文を整形して先頭だけ読む）
- 🔗 長文でも**途切れない**：全チャンクを合成しきってから1本に結合し、間を空けず一気に再生
- ⏩ 既定 1.2倍速（音程は変えず話速のみ）
- 🔕 `ZUNDA_DISABLE=1` でヘッドレス/バッチ実行時だけ黙らせられる

## 必要なもの

- **macOS**（`afplay` / `say` に依存）
- **Python 3**（標準ライブラリのみ。追加パッケージ不要）
- **[Claude Code](https://claude.com/claude-code)**
- **[VOICEVOX](https://voicevox.hiroshiba.jp/)**（ずんだもんの声を出すのに必要。engine が `http://localhost:50021` で待受）
  - 未導入・未起動でも動きますが、その場合は macOS 標準音声での読み上げになります。

## インストール

```sh
git clone https://github.com/TrueRyoB/cc-zundamon.git
cd cc-zundamon
./install.sh
```

`install.sh` は次を行います（冪等・`settings.json` はバックアップを取ってから編集）:

1. `hooks/say-summary.py` を `~/.claude/hooks/` へコピー
2. `~/.claude/settings.json` の `Stop` フックに登録
3. `python3` / VOICEVOX engine / `afplay` の有無をチェックして案内

インストール後、VOICEVOX を起動した状態で Claude Code に何か応答させると読み上げられます。

## `<speak>` で読み上げ内容を制御する

タグが無ければ本文を整形して先頭 ~220 文字を読みますが、**応答末尾に `<speak>...</speak>` を置くと、その中身だけ**を読み上げます。要約だけ喋らせたいときに便利です。

Claude に毎回付けさせたい場合は、`~/.claude/CLAUDE.md` に例えば次のように書きます:

```markdown
回答の末尾に <speak>...</speak> を付け、各項目 1〜2 行で要点だけを要約する。詳細は本文に置く。
```

## 設定（環境変数）

| 変数 | 既定 | 説明 |
|---|---|---|
| `ZUNDA_URL` | `http://localhost:50021` | VOICEVOX engine の URL |
| `ZUNDA_SPEAKER` | `3` | 話者 ID（3 = ずんだもん/ノーマル） |
| `ZUNDA_SPEED` | `1.2` | 再生速度（音程は変えず話速のみ） |
| `ZUNDA_MAX` | `220` | `<speak>` 無し時に読む最大文字数 |
| `ZUNDA_SPEAK_MAX` | `1200` | `<speak>` 有り時の最大文字数（暴走防止） |
| `ZUNDA_CHUNK` | `100` | 1 合成あたりの目安文字数 |
| `ZUNDA_SAY_VOICE` | `Kyoko` | フォールバック `say` の音声 |
| `ZUNDA_SAY_RATE` | `210` | フォールバック `say` の rate（実際は ×`ZUNDA_SPEED`） |
| `ZUNDA_DISABLE` | （未設定） | 立てるとナレーター無効。ヘッドレス/バッチ実行で使う |

環境変数は `~/.claude/settings.json` の `env` などで設定できます。

### ヘッドレス/バッチで黙らせる

`claude -p ...` のような自動実行で読み上げたくない場合、その実行の環境で `ZUNDA_DISABLE=1` を立てます:

```sh
ZUNDA_DISABLE=1 claude -p "$(cat prompt.md)" ...
```

## 仕組み

`Stop` フックとして起動し、`transcript_path` から**最後の assistant 応答**を読み取ります。
`<speak>` があればその中身、無ければコードブロック・表・URL・装飾を除いた本文を対象にし、
VOICEVOX で文単位に合成 → 1本に結合 → `afplay` で再生します。音声処理は別プロセスに切り離すため、
プロンプトの入力はブロックされません。

## アンインストール

```sh
./uninstall.sh
```

`settings.json` から登録を外し、コピーしたフックを削除します（`settings.json` はバックアップを取ってから編集）。

## 制限・トレードオフ

- **macOS 専用**（`afplay` / `say` 前提）。Linux/Windows は未対応。
- 「全部合成してから再生」する都合上、**最初の音が鳴るまでの待ち時間**は全文の合成完了まで延びます（合成速度は概ね ~0.1 秒/文字）。長すぎる場合は `<speak>` を短くするか `ZUNDA_SPEAK_MAX` を下げてください。

## ライセンス

[MIT](./LICENSE)
