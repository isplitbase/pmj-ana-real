# -*- coding: utf-8 -*-
"""
kessan_dates.py
決算年月日（今期・前期・前々期）と金額単位を、画像群（base64 data URL）から抽出するユーティリティ。

使用例:
    from kessan_dates import extract_closing_dates_and_unit
    closing_date, closing_date_zenki, closing_date_zenzenki, unit_str = \
        extract_closing_dates_and_unit(client, model_str, encoded_urls, start_time=start)
"""

from typing import List, Tuple
import json
import re
import time

def convert_japanese_date_to_western(text: str) -> str:
    """和暦「令和/平成/昭和/大正/明治」を西暦に変換する。該当しなければそのまま返す。"""
    if not isinstance(text, str):
        return text

    wareki_map = {
        "令和": 2018,
        "平成": 1988,
        "昭和": 1925,
        "大正": 1911,
        "明治": 1867
    }
    for era, base_year in wareki_map.items():
        if era in text:
            text = text.replace("元年", "1年")
            match = re.search(rf"{era}(\d+)年(\d+)月(\d+)日", text)
            if match:
                year = base_year + int(match.group(1))
                month = int(match.group(2))
                day = int(match.group(3))
                return f"{year}年{month:02d}月{day:02d}日"
    return text

def _normalize_unit(unit_str: str) -> str:
    """金額単位の表記ゆれを正規化。"""
    unit = (unit_str or "").strip()
    if unit == "千円":
        return "単位：千円"
    if unit == "万円":
        return "単位：万円"
    if unit == "百万円":
        return "単位：百万円"
    # そのまま返す（空欄や「円」「単位：千円」等も許容）
    return unit

def _build_messages(encoded_urls: List[str]) -> list:
    """OpenAI Chat API 用メッセージを構築。"""
    prompt_text = """
あなたは日本の財務諸表に詳しいAIです。
以下の画像から、記載されている今期・前期・前々期の「決算年月日」と、帳票における「金額単位」（例：1円、千円、万円、百万円など）を抽出してください。

出力は以下のようなJSON形式にしてください。日付が無い場合や金額単位が明記されていない場合は空欄で構いません：

{
  "今期": "2025年03月31日",
  "前期": "2024年03月31日",
  "前々期": "",
  "金額単位": "千円"
}

注意点:
- 日付が和暦で記載されている場合は、可能な限り西暦に変換してください。
- 同じ日付が複数の画像に記載されている場合でも重複して構いません。
- 1枚の画像に1つしか決算日が記載されていない場合、その日付が「今期」である可能性が高いです。
- 通常、決算日は「◯年◯月◯日」の形式で記載されます。期間（例：「2023年4月1日～2024年3月31日」）が書かれていた場合、終了日を「決算日」として扱ってください。
- 金額単位は「単位」「金額単位」「単位：千円」「単位：万円」「単位 百万円」などの表記を参考に判断してください。
- 複数画像をまとめて見て、総合的に判断してください。
""".strip()

    image_parts = [{"type": "image_url", "image_url": {"url": url, "detail": "auto"}} for url in encoded_urls]
    messages = [
        {"role": "system", "content": "あなたは財務諸表を正確に読み取るAIです。"},
        {"role": "user", "content": [{"type": "text", "text": prompt_text}] + image_parts}
    ]
    return messages

def extract_closing_dates_and_unit(
    client,
    model_str: str,
    encoded_urls: List[str],
    start_time: float | None = None
) -> Tuple[str, str, str, str]:
    """
    OpenAI に問い合わせて今期・前期・前々期の決算年月日と金額単位を抽出して返す。

    Returns:
        (closing_date, closing_date_zenki, closing_date_zenzenki, unit_str)
    """
    messages = _build_messages(encoded_urls)
    closing_date = ""
    closing_date_zenki = ""
    closing_date_zenzenki = ""
    unit_str = ""

    try:
        response = client.chat.completions.create(
            model=model_str,
            messages=messages,
            temperature=0.0,
            response_format={"type": "json_object"}
        )
        raw_text = response.choices[0].message.content.strip()
        print(f"\n【決算年月日の回答（全画像）】\n{raw_text}\n")

        # JSONパース
        date_result = json.loads(raw_text)

        # 和暦→西暦変換
        closing_date = convert_japanese_date_to_western(date_result.get("今期", "").strip())
        closing_date_zenki = convert_japanese_date_to_western(date_result.get("前期", "").strip())
        closing_date_zenzenki = convert_japanese_date_to_western(date_result.get("前々期", "").strip())

        print(f"\n🎯 決算年月日（今期）: {closing_date}")
        print(f"🎯 決算年月日（前期）: {closing_date_zenki}")
        print(f"🎯 決算年月日（前々期）: {closing_date_zenzenki}")

        unit_str = _normalize_unit(date_result.get("金額単位", ""))

    except Exception as e:
        print(f"⚠️ 決算年月日のAI抽出に失敗（全画像）: {e}")

    # 処理時間ログ（任意）
    if start_time is not None:
        try:
            print("＋＋処理時間決算年月日のAI出力:", time.time() - start_time)
        except Exception:
            pass

    return closing_date, closing_date_zenki, closing_date_zenzenki, unit_str
