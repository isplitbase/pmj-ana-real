# pmj-ana-real

本番系統(pmj-door-real から呼ばれる。149 用)。ソースは pmj-ana と同じで、既定値だけ違う(サービス名・マスタの場所)。

zaiTask「強力分析」用の Cloud Run サービス。決算書のページ画像を受け取り、
Analygent(ana) 本番と同じ方式で読み取って、PMJ の勘定科目マスタに当てはめた結果を
v2ac(iTaskScanPapers2)と同じ形で返す。設計は [DESIGN.md](DESIGN.md)。

| 系統 | 呼び出し元 | door | このサービス | 勘定科目マスタ |
|---|---|---|---|---|
| 検証 | test1 の make_ana.do | pmj-door | pmj-ana | gs://pmjbase/v2ac_kanjo_master.json |
| 本番 | 149 の make_ana.do | pmj-door-real | **pmj-ana-real** | gs://pmjbase/real/v2ac_kanjo_master.json |

## フォルダ

| 場所 | 中身 |
|---|---|
| `main.py` | 入口(Flask)。`/` `/ping` `/analyze` |
| `pmjana/classify.py` | ページの種類(ana の pdf-converter の写し。Azure Read OCR) |
| `pmjana/period.py` | 今期だけか今期・前期か(ana の getpdfinfo11 の写し) |
| `pmjana/runner.py` | ana の `callgpt.py` を同じ引数で実行し、結果を受け取る |
| `pmjana/gemini_local.py` | Gemini 呼び出し(ana-gemini-real の写し。別サービスを経由しない) |
| `pmjana/v2ac_account.py` | v2ac の勘定科目マスタ照合(account_DB)の写し |
| `pmjana/master.py` | マスタの読み込み(GCS)と照合 |
| `pmjana/houjin.py` | 法人: ana の結果 → v2ac の detail 行 |
| `pmjana/kojin.py` / `prompts/kojin_aoiro.txt` | 個人: 青色申告決算書 → 固定 96 行 |
| `ana/` | ana 本番(34.180.79.93 internal/for_8012, 2026-08-24)の読み取り処理とプロンプト。変更箇所は `[pmj-ana]` |
| `shim/mysql/` | callgpt.py の DB 書き込みを受け止める差し替え |

`ana/` の変更は最小限(直書きの API キーの削除、鍵ファイルが無いときは ADC、Gemini は直接呼ぶ)。

## 呼び出し(door 経由)

```
POST https://pmj-door-real-512697354748.asia-northeast1.run.app/call
{ "target": "ana", "path": "/analyze",
  "payload": { "images": ["<base64 JPEG>", ...], "doc_type": "houjin" } }
```

返り値の `result.format_info.cols[0].block_result` は v2ac と同じ(`detail[]` / `closing_date` / `company`)。
個人は `result.document_judgment_flag = "kojin"` と固定 96 行。

## Cloud Run の設定

- ソース: このリポジトリ(push で自動ビルド)
- メモリ 4GiB 以上・CPU 2、**同時実行数 1**、リクエストのタイムアウト 1800 秒
- 認証が必要。pmj-door-real のサービスアカウントに `roles/run.invoker`
- このサービスのサービスアカウントに: Cloud Vision API の利用、`pmjbase` の読み取り(`roles/storage.objectViewer`)
- pmj-door-real の環境変数に `TARGET_ANA=<このサービスの URL>`

| 環境変数 | 必須 | 内容 |
|---|---|---|
| OPENAI_API_KEY (または OPENAI_API_KEYS) | ○ | GPT(読み取り・期の判定・決算日・個人) |
| GEMINI_API_KEY | ○ | Gemini(損益計算書・貸借対照表の読み取り) |
| AZURE_KEY / AZURE_ENDPOINT | ○ | ページ分類の OCR(Azure Form Recognizer。未設定だと全ページ「BS or PL」扱い) |
| AZURE_API_KEY | ○ | T 字型の貸借対照表の読み取り(Azure Computer Vision。エンドポイントは ana と同じ zlite) |
| OPENAI_MODEL | | 既定 gpt-4.1-mini-2025-04-14(ana 本番と同じ) |
| GEMINI_MODEL | | 既定 models/gemini-3.1-pro-preview(ana と同じ。無ければ近い安定版へ自動切替) |
| MASTER_GCS_URI | | 既定 gs://pmjbase/real/v2ac_kanjo_master.json |
| PMJANA_CALLGPT_TIMEOUT | | 既定 1500 秒 |

設定の確認は `POST /ping`(AI は呼ばない。キーの有無とマスタの読み込みを返す)。
