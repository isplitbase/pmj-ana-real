# pmj-ana 設計 (強力分析)

zaiTask の一覧で案件をチェックして「強力分析」を押すと、Analygent(ana) と同じ方式で決算書を読み直し、
PMJ の勘定科目マスタに当てはめて、編集画面の項目(i_kanjo_info)を置き換える。

```
一覧「強力分析」(チェックした案件)
  → itask_ana_request.do : 新キュー i_itask_queue_ana に登録、m_itask.status=1(分析中)
  → cron 毎分 batch/ikisaki_itask_make_ana.do   ※ ikisaki_itask_make.do は修正しない
      ページ画像を読む(149=DB / test1=ファイル)
      → pmj-door(-real) → [pmj-ana(-real)] POST /analyze   ← 1回で全部行う
      → make.do の後処理(1330-2175)をコピーしたもので i_kanjo_info / i_aitask_top_info を置き換え
      → 成功: m_itask.status=9、i_aitask_top_info.status=0(精査待) / 失敗: m_itask.status=2(要確認)
```

| | 検証 | 本番 |
|---|---|---|
| door | pmj-door | pmj-door-real |
| このサービス | pmj-ana | pmj-ana-real |
| 勘定科目マスタ | gs://pmjbase/v2ac_kanjo_master.json | gs://pmjbase/real/v2ac_kanjo_master.json |

## pmj-ana の中身

元にするのは **ana 本番 (34.180.79.93 internal/for_8012, 2026-08-24 版)**。

1. **ページ画像の受け取り** — make_ana.do からページ画像(JPEG, base64)を受け取る
2. **ページの種類の判定** — pdf-converter と同じ: Azure Read で OCR → `_classify_page` / `_apply_extended_classification`
   (BS or PL / 販売費 / 製造原価 / 対象外)。ana で人が確認する画面は省く
3. **何期分か(P1/P2)の判定** — getpdfinfo11 と同じ: 画像を PDF にまとめて OpenAI に渡し、期のラベルを得る
4. **読み取り** — ana 本番の `callgpt.py` 一式をほぼそのまま実行する
   (Gemini / GPT×3 の多数決 / Google Vision の枠描画 / T 字 BS 判定 / 勘定科目名の補正 / 決算日)
   - DB(mysql) と進捗ファイルへの書き込みは、差し替え部品(`shim/`)で受け止める
   - Gemini は ana-gemini-real と同じ処理を、別サービスを経由せずにこの中で直接呼ぶ
5. **勘定科目マスタとの突き合わせ** — v2ac.py の `account_DB`(文字列の類似度)を移植。
   ana の `type`(BS/PL/販売費) と `分類` で範囲を絞る。並び順は ana の順。製造原価は除外
6. **個人(青色申告決算書)** — 個人用プロンプトで欄番号(①〜㊺)と BS を読み、固定 96 行の形にする。
   自由記入欄があふれたら雑費に加算。BS の期首は入れない。不動産・農業・白色は対象外(エラー)
7. **返す形** — v2ac (iTaskScanPapers2) と同じ `format_info.cols[0].block_result`
   (`detail[]`: candidate / amount_pre_year / amount_this_year / page / tabindex / db_exist / 座標=0、
   `closing_date`)。個人は `document_judgment_flag: "kojin"` を付ける

## 環境変数 (Cloud Run)

| 名前 | 用途 |
|---|---|
| OPENAI_API_KEY (または OPENAI_API_KEYS 改行/カンマ区切り) | GPT 読み取り・期の判定・決算日 |
| GEMINI_API_KEY | Gemini 読み取り |
| AZURE_KEY / AZURE_ENDPOINT | ページ分類の OCR (Form Recognizer prebuilt-read) |
| AZURE_API_KEY | T 字 BS の読み取り (Computer Vision) |
| OPENAI_MODEL | 既定 gpt-4.1-mini-2025-04-14 (ana 本番と同じ) |
| GEMINI_MODEL | 既定は ana-gemini-real と同じ |
| MASTER_GCS_URI | 既定 gs://pmjbase/v2ac_kanjo_master.json (-real は gs://pmjbase/real/...) |

Google Vision と GCS はサービスアカウント(ADC)で使う(Vision API・pmjbase の読み取り権限が必要)。
