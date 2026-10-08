# -*- coding: utf-8 -*-
"""ページの種類の判定(ana の pdf-converter の写し)

  元: ana/git/pdf-converter-real/main.py
    - call_azure_ocr / call_azure_ocr_with_retry   (923-1031)  Azure Form Recognizer prebuilt-read
    - _normalize_classify 〜 _apply_extended_classification (1034-1226)
    - main() の分類部分 (1600-1658) → classify_pages()
  ana では判定結果を人が画面で確認・修正するが、pmj-ana ではその確認を省いてそのまま使う。

  返す種類(ana の page_type_id):
    "1" = BS or PL / "2" = 対象外 / "3" = 販売費 / "4" = 製造原価
"""
import json
import os
import re
import time
import unicodedata
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

AZURE_KEY = os.environ.get("AZURE_KEY", "").strip()
AZURE_ENDPOINT = os.environ.get("AZURE_ENDPOINT", "").strip()

# pdf-converter では upload_files.php から常に true で渡される
READ_SGA = True
READ_MCR = True

PAGE_TYPE_IDS = {"BS or PL": "1", "対象外": "2", "販売費": "3", "製造原価": "4"}


def log_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


# =============================
# Azure OCR
# =============================
def call_azure_ocr(image_path: str) -> Dict[str, Any]:
    """
    Azure Form Recognizer (prebuilt-read) を呼び出す。
    azure_ai.py と同じ API・同じレスポンス形式。
    返り値: {"text_annotations": [{"description": "全テキスト"}]}

    タイムアウト方針（2026-05 改修）:
      - POST(解析リクエスト送信)        : 120 秒
      - ポーリング(1回ごと)             : 60 秒
      - ポーリング回数                  : 最大 120 回（≒最大 120 秒）
      （※実呼び出し側で指数バックオフリトライを行う）
    """
    global AZURE_KEY, AZURE_ENDPOINT
    if not AZURE_KEY or not AZURE_ENDPOINT:
        raise RuntimeError("AZURE_KEY または AZURE_ENDPOINT が未設定です")

    endpoint = AZURE_ENDPOINT.rstrip("/")
    # azure_ai.py と同じエンドポイント
    ocr_url = f"{endpoint}/formrecognizer/documentModels/prebuilt-read:analyze?api-version=2022-08-31"

    with open(image_path, "rb") as f:
        image_data = f.read()

    # Step1: POST → 202 + Operation-Location
    req = urllib.request.Request(
        ocr_url,
        data=image_data,
        headers={
            "Ocp-Apim-Subscription-Key": AZURE_KEY,
            "Content-Type": "application/octet-stream",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        operation_url = resp.headers.get("Operation-Location")

    if not operation_url:
        raise RuntimeError("Azure OCR: Operation-Location ヘッダーが取得できませんでした")

    # Step2: ポーリング（1秒ごと・最大 120 回 ≒ 約 120 秒）
    result = {}
    for _ in range(120):
        time.sleep(1)
        poll_req = urllib.request.Request(
            operation_url,
            headers={"Ocp-Apim-Subscription-Key": AZURE_KEY},
        )
        with urllib.request.urlopen(poll_req, timeout=60) as poll_resp:
            result = json.loads(poll_resp.read().decode("utf-8"))
        status = result.get("status")
        if status == "succeeded":
            break
        elif status == "failed":
            raise RuntimeError("Azure OCR 解析失敗（status=failed）")
        # "running" / "notStarted" → 続けてポーリング
    else:
        raise RuntimeError("Azure OCR タイムアウト（120秒超）")

    # Step3: azure_ai.py と同じ: analyzeResult.content から全テキスト取得
    azure_text = result["analyzeResult"]["content"]

    return {"text_annotations": [{"description": azure_text}]}


def call_azure_ocr_with_retry(image_path: str, max_attempts: int = 3) -> Dict[str, Any]:
    """
    call_azure_ocr() を指数バックオフ付きでリトライするラッパー。
      - 試行回数: 最大 max_attempts 回（既定 3）
      - バックオフ待機: 1.5秒 → 3秒 → 6秒（試行間で2倍）
      - 全試行失敗時は最後の例外を再送出する（呼び出し側で捕捉してデフォルト値を入れる想定）
    """
    delays = [1.5, 3.0, 6.0]
    last_err: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = call_azure_ocr(image_path)
            if attempt > 1:
                log_json({
                    "ok": True,
                    "stage": "azure_ocr_retry_succeeded",
                    "image": os.path.basename(image_path),
                    "attempt": attempt,
                })
            return result
        except Exception as e:
            last_err = e
            log_json({
                "ok": False,
                "stage": "azure_ocr_attempt_failed",
                "image": os.path.basename(image_path),
                "attempt": attempt,
                "max_attempts": max_attempts,
                "error": str(e),
            })
            if attempt < max_attempts:
                wait = delays[min(attempt - 1, len(delays) - 1)]
                log_json({
                    "ok": True,
                    "stage": "azure_ocr_backoff_sleep",
                    "image": os.path.basename(image_path),
                    "next_attempt": attempt + 1,
                    "sleep_seconds": wait,
                })
                time.sleep(wait)
    # 全試行失敗
    raise last_err if last_err is not None else RuntimeError("Azure OCR: 全リトライ失敗")


# =============================
# Page Classification  (upload_files_sub_8dj4.php 移植)
# =============================
def _normalize_classify(text: str) -> str:
    """全角/半角統一 + 空白除去 + 小文字化 + OCR揺らぎ補正（PHP $__normalize 移植）"""
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(r"\s+", "", text)  # 改行・タブ含む全空白除去
    # OCR 誤認識・旧字体の補正（PHP strtr と同等）
    _ocr_fix = {
        "販賣": "販売",
        "賣":   "売",
        "價":   "価",
        "及ヒ": "及び",
        "販売費及一般管理費": "販売費及び一般管理費",
    }
    for old, new in _ocr_fix.items():
        text = text.replace(old, new)
    return text.lower()


def _is_keyword_match(text: str, keyword: str, threshold: float = 1.0) -> bool:
    """PHP isKeywordMatch() の Python 移植（文字単位の順次マッチング）"""
    text = _normalize_classify(text)
    keyword = _normalize_classify(keyword)
    if not keyword:
        return True
    keyword_chars = list(keyword)
    text_chars = list(text)
    match_count = 0
    text_index = 0
    for char in keyword_chars:
        found = False
        while text_index < len(text_chars):
            if text_chars[text_index] == char:
                match_count += 1
                text_index += 1
                found = True
                break
            text_index += 1
        if not found:
            break
    return (match_count / len(keyword_chars)) >= threshold


def _contains_keyword_with_match(text: str, keywords: List[str]) -> Tuple[bool, Optional[str]]:
    for kw in keywords:
        if _is_keyword_match(text, kw):
            return True, kw
    return False, None


def _is_capital_change_like(
    text: str, keywords: List[str], threshold: float = 0.70
) -> Tuple[bool, Optional[str], Optional[str]]:
    lines = re.split(r"\r\n|\n|\r", text or "")
    for line in lines:
        normalized_line = _normalize_classify(line)
        for kw in keywords:
            if _is_keyword_match(normalized_line, kw, threshold):
                return True, kw, line
    return False, None, None


def _classify_page(text: str) -> Dict[str, Any]:
    """PHP classifyPage() の Python 移植"""
    bs_keywords = [
        "流動資産", "現金及び預金", "売掛金", "繰延資産", "資産の部",
        "流動負債", "買掛金", "短期借入金", "預り金", "固定負債", "負債の部",
        "純資産の部", "株主資本", "資本金", "資本剰余金", "利益剰余金",
        "その他利益剰余金", "評価換算差額等", "新株予約権", "純資産の部合計",
        "純資産合計", "負債純資産の部合計",
    ]
    pl_keywords = [
        "売上高", "売上原価", "期首棚卸高", "仕入高", "期末棚卸高", "売上総利益",
        "営業利益", "営業損失", "営業外収益", "受取利息", "受取配当金", "営業外費用",
        "支払利息", "特別利益", "特別損失", "税引前当期利益", "税引前当期損失",
        "法人税住民税及び事業税", "当期純利益", "当期純損失", "その他収益",
        "当期収益", "当期損失",
    ]
    cf_keywords = [
        "資本等変動計算書", "株主資本等変動計算書", "連結株主資本等変動計算書",
        "連結持分変動計算書", "一般管理費の計算内訳", "一般管理費計算内訳",
        "管理費の計算内訳", "管理費計算内訳", "棚卸資産の計算内訳", "棚卸資産計算内訳",
    ]

    cf_hit, cf_kw, cf_line = _is_capital_change_like(text, cf_keywords, 0.70)
    if cf_hit:
        return {
            "type": "対象外",
            "firstHalfMatch": [],
            "secondHalfMatch": [],
            "cfKeywords": "NG",
            "cfMatchedKeyword": cf_kw,
            "cfMatchedLine": cf_line,
        }

    lines = re.split(r"\r\n|\n|\r", text or "")
    half = -(-len(lines) // 2)  # ceil
    first_half = "\n".join(lines[:half])
    second_half = "\n".join(lines[half:])

    match_info: Dict[str, Any] = {"type": "", "firstHalfMatch": [], "secondHalfMatch": []}
    first_bs, m_fbs = _contains_keyword_with_match(first_half, bs_keywords)
    first_pl, m_fpl = _contains_keyword_with_match(first_half, pl_keywords)
    second_bs, m_sbs = _contains_keyword_with_match(second_half, bs_keywords)
    second_pl, m_spl = _contains_keyword_with_match(second_half, pl_keywords)

    if first_bs and m_fbs:
        match_info["firstHalfMatch"].append(m_fbs)
    if first_pl and m_fpl:
        match_info["firstHalfMatch"].append(m_fpl)
    if second_bs and m_sbs:
        match_info["secondHalfMatch"].append(m_sbs)
    if second_pl and m_spl:
        match_info["secondHalfMatch"].append(m_spl)

    if first_bs and second_pl:
        match_info["type"] = "BS=>PL"
    elif first_pl and second_bs:
        match_info["type"] = "PL=>BS"
    elif first_bs or second_bs:
        match_info["type"] = "BS"
    elif first_pl or second_pl:
        match_info["type"] = "PL"
    else:
        match_info["type"] = "対象外"

    return match_info


def _apply_extended_classification(
    print_images: List[Dict],
    image_txt: List[Dict],
    read_sga: bool,
    read_mcr: bool,
) -> None:
    """PHP の read_sga/read_mcr 拡張分類ブロックの Python 移植"""
    if not (read_sga or read_mcr):
        return

    mfg_titles = ["製造原価報告書", "製造原価の報告書"]
    mfg_required = [
        "当期製造原価", "当期総製造費用", "期首仕掛品", "期末仕掛品",
        "仕掛品", "材料費", "労務費", "製造間接費", "製造原価",
        "加工費", "製造部門", "月別製造原価",
    ]
    neg_strong = ["貸借対照表", "balance sheet", "balancesheet", "資産の部", "負債の部", "純資産の部"]

    for i, info in enumerate(print_images):
        cur_type = info["page_type"]["type"]
        raw_text = ""
        if i < len(image_txt):
            ocr = image_txt[i]
            if isinstance(ocr, dict) and ocr.get("text_annotations"):
                raw_text = ocr["text_annotations"][0].get("description", "")

        t = _normalize_classify(raw_text)
        if not t:
            # PHPでは '不明' → 即座に '対象外' で上書きされるため実質 '対象外'
            info["page_type"]["type"] = "対象外"
            continue

        # 1) 販売費及び一般管理費
        has_han = _is_keyword_match(t, _normalize_classify("販売費"), 0.86)
        has_ipp = (
            _is_keyword_match(t, _normalize_classify("一般管理"), 0.86)
            or _is_keyword_match(t, _normalize_classify("一般管理費"), 0.86)
        )
        if has_han and has_ipp:
            if read_sga and cur_type != "BS or PL":
                info["page_type"]["type"] = "販売費"
            continue

        # 2-1) 製造原価報告書タイトル一致
        mfg_title_hit = any(
            _is_keyword_match(t, _normalize_classify(ttl), 0.86) for ttl in mfg_titles
        )
        if mfg_title_hit:
            if read_mcr:
                info["page_type"]["type"] = "製造原価"
            continue

        # 2-2) 必須キーワード3語以上 + 強否定なし
        hits = sum(1 for kw in mfg_required if _is_keyword_match(t, _normalize_classify(kw), 0.85))
        has_neg = any(_is_keyword_match(t, _normalize_classify(ng), 0.90) for ng in neg_strong)
        if hits >= 3 and not has_neg:
            if read_mcr:
                info["page_type"]["type"] = "製造原価"
            continue

        if cur_type == "BS or PL":
            continue
        info["page_type"]["type"] = "対象外"


# =============================


# =============================
# [pmj-ana] main() の分類部分(1600-1658)を関数にしたもの
# =============================
def classify_pages(image_paths: List[str]) -> Dict[str, Any]:
    """各ページを OCR して種類を判定する。

    返り値: {"types": ["1","3",...], "names": ["BS or PL","販売費",...], "ocr_texts": [...], "ocr_failed": [...]}
    Azure が未設定・失敗のページは pdf-converter と同じく "BS or PL" とする。
    """
    all_ocr_results = []
    for p in image_paths:
        if not AZURE_KEY or not AZURE_ENDPOINT:
            # 未設定ならリトライで待たずに、OCR 失敗と同じ扱い("BS or PL")にする
            all_ocr_results.append({"text_annotations": [{"description": ""}], "_ocr_failed": True})
            continue
        try:
            all_ocr_results.append(call_azure_ocr_with_retry(p))
        except Exception as e:
            log_json({"ok": False, "stage": "azure_ocr_failed", "image": os.path.basename(p), "error": str(e)})
            all_ocr_results.append({"text_annotations": [{"description": ""}], "_ocr_failed": True})

    page_classifications = []
    for ocr in all_ocr_results:
        text = ocr["text_annotations"][0].get("description", "") if ocr.get("text_annotations") else ""
        if ocr.get("_ocr_failed"):
            page_classifications.append({"type": "BS or PL", "_ocr_failed_default": True})
        else:
            page_classifications.append(_classify_page(text))

    print_images = []
    for i, cls in enumerate(page_classifications):
        info = {"page_type": dict(cls), "rotation": 0, "page_no": i + 1}
        # 対象外以外は "BS or PL" に統一(PHP 準拠)
        if info["page_type"]["type"] != "対象外":
            info["page_type"]["type"] = "BS or PL"
        print_images.append(info)

    # 拡張分類(販売費・製造原価)。OCR に失敗したページは "BS or PL" のまま残す
    ok_idx = [i for i, o in enumerate(all_ocr_results) if not o.get("_ocr_failed")]
    _apply_extended_classification([print_images[i] for i in ok_idx], [all_ocr_results[i] for i in ok_idx], READ_SGA, READ_MCR)

    names = [p["page_type"]["type"] for p in print_images]
    return {
        "types": [PAGE_TYPE_IDS.get(n, "2") for n in names],
        "names": names,
        "ocr_texts": [(o["text_annotations"][0].get("description", "") if o.get("text_annotations") else "") for o in all_ocr_results],
        "ocr_failed": [bool(o.get("_ocr_failed")) for o in all_ocr_results],
    }
