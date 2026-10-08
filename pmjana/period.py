# -*- coding: utf-8 -*-
"""何期分の決算書か(今期だけ / 今期・前期)の判定(ana の getpdfinfo11 の写し)

  元: ana/git/ana-getpdfinfo-real/.../originals/getpdfinfo11.py
    - build_meta_prompt  (78-182) そのまま
    - _extract_json_text (184-190) そのまま
    - _call_openai_json  (193-240) そのまま(MODEL は環境変数 OPENAI_MODEL)
  ana では PDF ファイルごとに期を判定し、upload_files.php が postingPeriod を決める
  (ラベル1つ → currentTermBalanceSheet、2つ → currentAndPreviousBalanceSheet)。
  PMJ は1案件1ファイルなので、ページ画像を1つの PDF にまとめて1回だけ判定する。
"""
import base64
import io
import json
import os
from typing import List

import openai as _openai_module
from PIL import Image

MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini-2025-04-14")


def build_meta_prompt(pdf_infos: list) -> str:
    """
    pdf_infos = [
      {"index": 1, "file_name": "A.pdf"},
      {"index": 2, "file_name": "B.pdf"},
      ...
    ]
    """
    file_list_text = "\n".join(
        [f"- PDF{p['index']} = {p['file_name']}" for p in pdf_infos]
    )

    return f"""
あなたは日本の決算書を読み取る専門家です。

これから複数のPDFが送られます。
各PDFには番号とファイル名があります。
必ずその対応関係を守って判定してください。

PDF一覧:
{file_list_text}

目的:
各PDFが「今期」「前期」「前々期」「前々期の前期」のどれに該当するか判定してください。

特別パターンサンプル1：
　1枚目PDF（pdf1.pdf）：[N]年度と[N+1]年度の情報が含まれている
　2枚目PDF（pdf2.pdf）：[N+2]年度と[N+3]度の情報が含まれている
　の場合
　1枚目PDF（pdf1.pdf）：「前々期」/「前々期の前期」
　2枚目PDF（pdf2.pdf）：「今期」/「前期」

特別パターンサンプル2：
　1枚目PDF（pdf1.pdf）：令和6年9月の貸借対照表と損益計算書が含まれている
　2枚目PDF（pdf2.pdf）：令和6年12月の貸借対照表と損益計算書が含まれている
　3枚目PDF（pdf3.pdf）：令和7年9月の貸借対照表と損益計算書が含まれている
　の場合
　1枚目PDF（pdf1.pdf）：「前々期」
　2枚目PDF（pdf2.pdf）：「前期」
　3枚目PDF（pdf3.pdf）：「今期」

特別パターンサンプル3：
　1枚目PDF（pdf1.pdf）：令和6年1月の貸借対照表と損益計算書が含まれている
　2枚目PDF（pdf2.pdf）：令和6年9月の貸借対照表と損益計算書が含まれている
　3枚目PDF（pdf3.pdf）：令和7年9月の貸借対照表と損益計算書が含まれている
　の場合
　1枚目PDF（pdf1.pdf）：「前々期」
　2枚目PDF（pdf2.pdf）：「前期」
　3枚目PDF（pdf3.pdf）：「今期」

特別パターンサンプル4（単一ファイル内に複数期の金額列があるケース／TKC書式など）：
　1枚目PDF（pdf1.pdf）：1つの損益計算書または貸借対照表の表内に
　　「前期 額」「決算 額（＝当期）」のように複数の金額列が左右に並んでいる
　　（タイトルには当期の期間だけが記載され、前期列には年度が印字されていないことが多い）
　の場合
　1枚目PDF（pdf1.pdf）：「今期」「前期」（＝表内の金額列の数だけ期がある＝この例では2期）
　※「決算」「当期」「本年」などの列＝今期、「前期」「前年」などの列＝前期 として扱う。
　※前期列に年度の記載が無くても、当期の1期前（当期の年度−1年）として必ず数えること。

「プロンプトの特別パターンサンプルはあくまで形式を教えるためのものであり、日付などの事実は必ずPDF内の記載から抽出すること」

重要ルール:
- 各PDFは同じ企業の決算書です。
- 各PDFは複数期を含む可能性があります。
- 販売費及び一般管理費の資料を見ないでください。
- たな卸資産の資料を見ないでください。
- 製造原価の資料を見ないでください。
- ★重要★ 1つの財務諸表（損益計算書・貸借対照表）の表内に、複数の金額列（例:「前期 額」と「決算 額」、「前年」と「本年」、「当期」と「前期」など）が並んでいる場合は、その金額列の数だけ決算期があるとして必ず数えること。日付が印字されている列の数ではなく、実際の金額列の数で期数を数える。
- 前期列・前年列に年度（年月）が印字されていなくても、当期（決算・本年）の1期前として必ず「前期」に数えること。年度は当期の年度−1年として補完してよい。
- ファイル数は1且つ、表内の金額列も本当に1列しかない場合に限り、必ず今期になる。（前期列が別途存在する場合は1年度とみなさないこと）
- その場合は、そのPDFに含まれる期ラベルをすべて列挙してください。
- PDF番号とファイル名を取り違えないでください。
- 出力は必ずJSONのみを返してください。説明文やMarkdownは不要です。
- 決算情報がある年度をすべて出してください。
- 「今期」「前期」「前々期」「前々期の前期」をすべてのPDFをトータル見て判断してください
- すべてのPDFの中の最新決算期間は「今期」です
- ラベルは必ず次の4種類のみを使ってください: ["今期", "前期", "前々期", "前々期の前期"]
- 必ず各PDFごとに reason を返してください
- reason には、どの年度・決算年月・相対比較によりそのラベルになったかを簡潔に書いてください
- 順番は、送信されたPDF順（PDF1, PDF2, PDF3, ...）で返してください。

出力JSON形式:
{{
  "results": [
    {{
      "pdf_index": 1,
      "file_name": "A.pdf",
      "labels": ["今期"],
      "reason": "令和7年9月期が全PDF中で最新のため今期",
      "年度": ["令和7年度"]
    }},
    {{
      "pdf_index": 2,
      "file_name": "B.pdf",
      "labels": ["前期"],
      "reason": "令和6年9月期で、全PDF中の最新期の1期前に当たるため前期",
      "年度": ["令和6年度"]
    }}
  ]
}}
""".strip()


# ────────────────────────────────
# OpenAI API: JSON抽出ヘルパー（リトライ付き）
# ────────────────────────────────
def _extract_json_text(text: str) -> str:
    text = text.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0].strip()
    return text


def _call_openai_json(client: _openai_module.OpenAI, messages: list, max_tokens: int = 4000) -> dict:
    import time as _time

    MAX_RETRIES = 3
    last_err = None

    for attempt in range(MAX_RETRIES):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                temperature=0.0,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )

            text = None
            try:
                text = response.choices[0].message.content
            except Exception:
                pass

            print(f"[INFO] 🤖 AIレスポンス(raw, {len(text) if text else 0}文字): {text}", flush=True)

            if not text:
                raise ValueError("AI APIレスポンス取得失敗")

            text = _extract_json_text(text)
            data = json.loads(text)

            if not isinstance(data, dict):
                raise ValueError("AIのJSON応答がdictではありません")

            if "results" not in data or not isinstance(data["results"], list):
                raise ValueError("AIのJSON応答に results がありません")

            return data

        except Exception as e:
            last_err = e
            wait = 2 ** attempt
            print(f"[WARN] AI API retry {attempt + 1}/{MAX_RETRIES}: {e}")
            _time.sleep(wait)

    raise RuntimeError(f"AI API {MAX_RETRIES}回失敗: {last_err}")


# ────────────────────────────────


# ────────────────────────────────
# [pmj-ana] ページ画像 → 1つの PDF → 期の判定
# ────────────────────────────────
def _images_to_pdf_bytes(image_paths: List[str], max_side: int = 1754) -> bytes:
    """ページ画像を1つの PDF にする(pdf-converter の縮小と同じく長辺 1754px に抑える)"""
    pages = []
    for p in image_paths:
        im = Image.open(p).convert("RGB")
        w, h = im.size
        if max(w, h) > max_side:
            r = max_side / float(max(w, h))
            im = im.resize((int(w * r), int(h * r)))
        pages.append(im)
    buf = io.BytesIO()
    pages[0].save(buf, format="PDF", save_all=True, append_images=pages[1:], resolution=150)
    return buf.getvalue()


def detect_upload_key(image_paths: List[str], api_key: str) -> dict:
    """返り値: {"upload_key": "currentTermBalanceSheet" | "currentAndPreviousBalanceSheet", "labels": [...], "raw": {...}}

    ラベルが3つ以上(前々期まで)の場合も、PMJ では今期・前期だけを使うので currentAndPreviousBalanceSheet にする
    (ana の前々期用の処理は別の Cloud Run を呼ぶため使わない)。
    """
    pdf_bytes = _images_to_pdf_bytes(image_paths)
    file_name = "kessansho.pdf"
    prompt = build_meta_prompt([{"index": 1, "file_name": file_name}])
    content_parts = [
        {"type": "text", "text": prompt},
        {"type": "text", "text": f"以下が PDF1 / ファイル名: {file_name} です。"},
        {"type": "file", "file": {"filename": file_name,
                                  "file_data": "data:application/pdf;base64," + base64.b64encode(pdf_bytes).decode("utf-8")}},
    ]
    client = _openai_module.OpenAI(api_key=api_key)
    result = _call_openai_json(client, [{"role": "user", "content": content_parts}])
    labels = []
    for item in result.get("results", []):
        lv = item.get("labels") or []
        if isinstance(lv, str):
            lv = [lv]
        labels.extend([x for x in lv if isinstance(x, str)])
    labels = list(dict.fromkeys(labels))
    key = "currentAndPreviousBalanceSheet" if len(labels) >= 2 else "currentTermBalanceSheet"
    return {"upload_key": key, "labels": labels, "raw": result}
