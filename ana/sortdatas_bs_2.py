# -*- coding: utf-8 -*-
"""
Colab / Python 用（汎用・安全マージ＋分類バリデーション＆“再構成”・同義語統合の最終版）

改善点（重要）
- 金額は列をまたがない：前期ファイルの金額→前期列、前々期→前々期列のみ。
- 分類チェック＆再構成：資産（流動→小計→固定→小計→資産の部合計）→負債（流動→小計→固定→小計→負債の部合計）
  →純資産（株主資本→株主資本合計→純資産の部合計）→最終（負債・純資産の部合計）の順に**再組立て**。
  ※「有形固定資産／無形固定資産／投資その他の資産／出資金」は**必ず固定資産ブロック**へ。
- 同義語（例：「現金預金」「現金・預金合計」）は「現金及び預金」に正規化して重複統合。
- 標準出力は {"list":[...]} のみ。保存先：/mnt/data/bs_merged_validated.json
"""

import json
import os
import re
import unicodedata
from typing import Optional, Tuple, Any, Dict, List

# ========= 設定 =========
SHOW_TABLE = False  # TrueにするとColab上で表を表示（標準出力のJSONは常に出ます）

# 直接貼付で使う場合は以下に JSON を入れる（配列 or {"list":[...]}）
JSON_CURRENT_TEXT = ""   # 今期
JSON_PREV_TEXT    = ""   # 前期
JSON_PRIOR_TEXT   = ""   # 前々期

# 自動探索するファイル名候補
CURRENT_FILE_CANDIDATES = ["BS_今期.json", "bs_今期.json", "BS_current.json", "current_bs.json"]
PREV_FILE_CANDIDATES    = ["BS_前期.json", "bs_前期.json", "BS_prev.json", "previous_bs.json"]
PRIOR_FILE_CANDIDATES   = ["BS_前々期.json", "bs_前々期.json", "BS_prior.json", "prior_bs.json"]
SEARCH_DIRS = ["/mnt/data", "/content", "content", "/"]
FILE_BS_INPUT = "/content/bs_input.json"

# ========= 正規化・同義語 =========
SYNONYM_MAP = {
    # 合計見出しの正規化
    "資産の部": "資産の部合計",
    "資産合計": "資産の部合計",
    "負債の部": "負債の部合計",
    "負債合計": "負債の部合計",
    "純資産の部": "純資産の部合計",
    "純資産合計": "純資産の部合計",
    "負債及び純資産の部": "負債・純資産の部合計",
    "負債及び純資産合計": "負債・純資産の部合計",
    "負債純資産合計": "負債・純資産の部合計",
    "負債・純資産合計": "負債・純資産の部合計",
    # かっこ表記の除去
    "【流動資産】": "流動資産",
    "【固定資産】": "固定資産",
    "【流動負債】": "流動負債",
    "【固定負債】": "固定負債",
    "【株主資本】": "株主資本",
    "【資本金】": "資本金",
    "【利益剰余金】": "利益剰余金",
    # 名称揺れ
    "現金・預金": "現金及び預金",
    "現金預金": "現金及び預金",
    "現金・預金合計": "現金及び預金",
    "現金及び預金合計": "現金及び預金",
    "(有形固定資産)": "有形固定資産",
    "(その他利益剰余金)": "その他利益剰余金(純資産の部)",
    "その他(純資産の部)": "その他利益剰余金(純資産の部)",
}

TOTAL_KEYS = ["資産の部合計", "負債の部合計", "純資産の部合計", "負債・純資産の部合計"]
FINAL_TOTAL_KEY = "負債・純資産の部合計"

# セクション→その属する部合計（終端）
SECTION_TOTAL_OF = {
    # 資産
    "流動資産": "資産の部合計",
    "固定資産": "資産の部合計",
    "資産合計": "資産の部合計",
    # 負債
    "流動負債": "負債の部合計",
    "固定負債": "負債の部合計",
    "負債合計": "負債の部合計",
    # 純資産
    "株主資本": "純資産の部合計",
    "資本金": "純資産の部合計",
    "利益剰余金": "純資産の部合計",
    "繰越利益剰余金": "純資産の部合計",
    "純資産合計": "純資産の部合計",
    # 固定資産配下は必ず資産ブロック
    "有形固定資産": "資産の部合計",
    "無形固定資産": "資産の部合計",
    "投資その他の資産": "資産の部合計",
}

# 分類ヒント（欠落補完）
ACCOUNT_CLASS_HINT = {
    # 流動資産
    "現金及び預金": "流動資産", "売掛金": "流動資産", "商品": "流動資産",
    "未収入金": "流動資産", "前払費用": "流動資産", "たな卸資産": "流動資産",
    "当座資産": "流動資産", "立替金": "流動資産", "その他(流動資産)": "流動資産",
    # 固定資産（＝非流動資産）
    "有形固定資産": "有形固定資産", "無形固定資産": "無形固定資産",
    "投資その他の資産": "投資その他の資産", "出資金": "投資その他の資産",
    # 流動負債
    "預り金": "流動負債", "未払金": "流動負債", "買掛金": "流動負債",
    "短期借入金": "流動負債", "未払消費税": "流動負債", "未払法人税等": "流動負債",
    # 固定負債
    "長期借入金": "固定負債", "役員借入金": "固定負債",
    # 純資産
    "資本金": "資本金", "利益剰余金": "利益剰余金", "繰越利益剰余金": "繰越利益剰余金",
    "株主資本": "株主資本",
}

SECTION_HEADERS = [
    "流動資産", "固定資産",
    "流動負債", "固定負債",
    "株主資本", "資本金", "利益剰余金", "繰越利益剰余金",
    "有形固定資産", "無形固定資産", "投資その他の資産",
]

AMOUNT_KEYS_PRIMARY = ["金額", "amount", "value"]
AMOUNT_KEY_PATTERNS = [r"^金額$", r"金額", r"^amount$", r"^value$"]

# 期間ラベル優先順（ファイル別）
CURRENT_LABEL_ORDER = ["今期", "当期", "本期", "current"]
PREV_LABEL_ORDER    = ["前期", "今期", "当期", "本期", "current"]
PRIOR_LABEL_ORDER   = ["前々期", "今期", "当期", "本期", "current"]

# ========= ユーティリティ =========
def is_blank(x: Any) -> bool:
    return x is None or (isinstance(x, str) and x.strip() == "")

def parse_amount(x: Any) -> Optional[int]:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        try:
            return int(x)
        except Exception:
            return None
    if not isinstance(x, str):
        return None
    s = unicodedata.normalize("NFKC", x).strip()
    if s == "" or s.lower() in ("nan", "null", "none"):
        return None
    negative = False
    if (s.startswith("(") and s.endswith(")")) or (s.startswith("（") and s.endswith("）")):
        negative = True; s = s[1:-1]
    if s.startswith("△") or s.startswith("▲"):
        negative = True; s = s[1:]
    s = s.replace("円","").replace("¥","").replace(",","").replace(" ","").replace("\u3000","")
    s = re.sub(r"[^0-9\-\+\.]", "", s)
    if s == "" or s in ("-","+",".","+.","-."):
        return None
    try:
        val = float(s)
        if negative:
            val = -val
        return int(val)
    except Exception:
        return None

def _match_first_key(d: dict, patterns: List[str]) -> Optional[str]:
    for k in d.keys():
        k_norm = unicodedata.normalize("NFKC", str(k)).strip()
        for pat in patterns:
            if re.search(pat, k_norm):
                return k
    return None

def _extract_from_period_dict(v: dict) -> Tuple[Optional[int], Optional[Any]]:
    for key in AMOUNT_KEYS_PRIMARY:
        if key in v:
            amt = parse_amount(v.get(key))
            pno = v.get("page_no") or v.get("page") or v.get("pageNo") or v.get("ページ")
            if not is_blank(amt):
                return amt, pno
    mkey = _match_first_key(v, AMOUNT_KEY_PATTERNS)
    if mkey:
        amt = parse_amount(v.get(mkey))
        pno = v.get("page_no") or v.get("page") or v.get("pageNo") or v.get("ページ")
        if not is_blank(amt):
            return amt, pno
    return None, None

def normalize_name(raw: str) -> str:
    if raw is None:
        return ""
    s = unicodedata.normalize("NFKC", str(raw)).strip()
    def strip_outer(x: str) -> str:
        if len(x) >= 2 and x[0] in "【[" and x[-1] in "】]":
            return x[1:-1].strip()
        if len(x) >= 2 and ((x[0]=="(" and x[-1]==")") or (x[0]=="（" and x[-1]=="）")):
            return x[1:-1].strip()
        return x
    s2 = strip_outer(s)
    s3 = SYNONYM_MAP.get(s, SYNONYM_MAP.get(s2, s2))
    return unicodedata.normalize("NFKC", s3).strip()

def normalize_classification(name: str, given: Optional[str]) -> Optional[str]:
    if name == "資産の部合計": return "資産合計"
    if name == "負債の部合計": return "負債合計"
    if name == "純資産の部合計": return "純資産合計"
    if name == "負債・純資産の部合計": return "負債純資産合計"
    if given and given.strip():
        return given
    return ACCOUNT_CLASS_HINT.get(name, given)

def to_output_row(name_norm: str, klass: Optional[str], typ: Optional[str]) -> dict:
    return {
        "勘定科目": name_norm,
        "分類": klass if klass is not None else "",
        "type": typ if typ is not None else "BS",
        "今期":   {"金額": None, "page_no": None},
        "前期":   {"金額": None, "page_no": None},
        "前々期": {"金額": None, "page_no": None},
    }

# ========= 入力ロード =========
def _find_file(candidates: List[str]) -> Optional[str]:
    cand_paths = []
    for dn in SEARCH_DIRS:
        for nm in candidates:
            cand_paths.append(os.path.join(dn, nm))
            cand_paths.append(os.path.join(dn, nm.replace(".json", ".JSON")))
    for p in cand_paths:
        try:
            if os.path.exists(p):
                return p
        except Exception:
            pass
    return None

def _json_to_rows(obj) -> list:
    if isinstance(obj, dict):
        return obj.get("list", [])
    if isinstance(obj, list):
        return obj
    return []

def _load_json_array_from_text_or_path(text: str, candidates: List[str]) -> list:
    if text and text.strip():
        return _json_to_rows(json.loads(text))
    p = _find_file(candidates)
    if p and os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return _json_to_rows(json.load(f))
        except Exception:
            pass
    return []

# ========= 金額抽出（ファイル種別ごと） =========
def extract_amount_page_with_order(item: dict, label_order: List[str]) -> Tuple[Optional[int], Optional[Any]]:
    for k in label_order:
        v = item.get(k)
        if isinstance(v, dict):
            amt, pno = _extract_from_period_dict(v)
            if not is_blank(amt):
                return amt, pno
    for key in AMOUNT_KEYS_PRIMARY:
        if key in item:
            amt = parse_amount(item.get(key))
            pno = item.get("page_no") or item.get("page") or item.get("pageNo") or item.get("ページ")
            if not is_blank(amt):
                return amt, pno
    for key in ["amount", "value"]:
        v = item.get(key)
        if isinstance(v, dict):
            cand = v.get("amount", v.get("value"))
            amt = parse_amount(cand)
            pno = v.get("page_no") or v.get("page") or v.get("pageNo") or v.get("ページ")
            if not is_blank(amt):
                return amt, pno
    return None, None

# ========= 重複・統合 =========
def deduplicate_totals(rows: list) -> list:
    seen = set(); filtered = []
    for r in rows:
        name = r["勘定科目"]
        if name in TOTAL_KEYS:
            if name in seen:
                continue
            seen.add(name)
        filtered.append(r)
    return filtered

def merge_duplicates_preserve_current_order(rows: List[dict]) -> List[dict]:
    name_first_idx: Dict[str, int] = {}
    remove_idx: List[int] = []
    for i, r in enumerate(rows):
        nm = r["勘定科目"]
        if nm not in name_first_idx:
            name_first_idx[nm] = i
        else:
            base = rows[name_first_idx[nm]]
            dup = r
            for prd in ("今期", "前期", "前々期"):
                if base[prd]["金額"] is None and dup[prd]["金額"] is not None:
                    base[prd] = dup[prd]  # 列またぎなし
            if not base.get("分類"):
                base["分類"] = dup.get("分類", "")
            if not base.get("type"):
                base["type"] = dup.get("type", "BS")
            remove_idx.append(i)
    for i in sorted(remove_idx, reverse=True):
        rows.pop(i)
    return rows

# ========= “再構成”のためのバケット化 =========
def bucket_for(name: str, klass: str) -> str:
    """行を大区分に振り分け（名称ヒント優先 → 分類）。"""
    n = name or ""; k = klass or ""
    # major totals / subtotals are別扱い（再構成側で配置）
    if n in TOTAL_KEYS:
        return "major_total"
    # 固定資産配下は**名称で**固定資産に強制
    if n in ("有形固定資産", "無形固定資産", "投資その他の資産", "出資金"):
        return "assets_noncurrent"
    # 分類による判定
    if k in ("流動資産",):
        return "assets_current"
    if k in ("固定資産", "有形固定資産", "無形固定資産", "投資その他の資産"):
        return "assets_noncurrent"
    if k in ("流動負債",):
        return "liab_current"
    if k in ("固定負債",):
        return "liab_noncurrent"
    if k in ("株主資本", "資本金", "利益剰余金", "繰越利益剰余金"):
        return "equity"
    # 名称ヒント（資産/負債/純資産）
    if "負債" in n:
        return "liab_current" if "流動" in n else "liab_noncurrent"
    if n in ("株主資本", "資本金", "利益剰余金", "繰越利益剰余金", "株主資本合計"):
        return "equity"
    if "資産" in n:
        return "assets_noncurrent" if ("固定" in n or "投資" in n or "有形" in n or "無形" in n) else "assets_current"
    # 不明は資産（流動）にフォールバック（迷子を純資産側に行かせない）
    return "assets_current"

def canonical_rebuild(rows: List[dict]) -> List[dict]:
    """
    既存の並びに依存せず、会計上の正順で**再構成**する。
    """
    # 1) 正規化＆分類補完
    norm_rows = []
    for idx, r in enumerate(rows):
        name = normalize_name(r.get("勘定科目"))
        klass = normalize_classification(name, r.get("分類"))
        if not klass:
            klass = ACCOUNT_CLASS_HINT.get(name, "")
        rr = dict(r)
        rr["勘定科目"] = name
        rr["分類"] = klass or ""
        rr["_orig_idx"] = idx
        norm_rows.append(rr)

    # 2) ヘッダ・小計・部合計を確保
    headers = { "流動資産": None, "固定資産": None, "流動負債": None, "固定負債": None, "株主資本": None }
    subtotals = { "流動資産合計": None, "固定資産合計": None, "流動負債合計": None, "固定負債合計": None, "株主資本合計": None }
    majors   = { "資産の部合計": None, "負債の部合計": None, "純資産の部合計": None, "負債・純資産の部合計": None }

    buckets = { k: [] for k in ("assets_current","assets_noncurrent","liab_current","liab_noncurrent","equity","other") }

    for r in norm_rows:
        n = r["勘定科目"]
        if n in headers: headers[n] = r; continue
        if n in subtotals: subtotals[n] = r; continue
        if n in majors: majors[n] = r; continue
        b = bucket_for(n, r["分類"])
        if b in buckets:
            buckets[b].append(r)
        else:
            buckets["other"].append(r)

    # バケット内は出現順を保持
    for b in buckets.values():
        b.sort(key=lambda x: x["_orig_idx"])

    # 3) 再構成
    new_rows: List[dict] = []

    # --- 資産 ---
    if headers["流動資産"]: new_rows.append(headers["流動資産"])
    new_rows.extend(buckets["assets_current"])
    if subtotals["流動資産合計"]: new_rows.append(subtotals["流動資産合計"])

    if headers["固定資産"]: new_rows.append(headers["固定資産"])
    new_rows.extend(buckets["assets_noncurrent"])
    if subtotals["固定資産合計"]: new_rows.append(subtotals["固定資産合計"])

    if majors["資産の部合計"]: new_rows.append(majors["資産の部合計"])

    # --- 負債 ---
    if headers["流動負債"]: new_rows.append(headers["流動負債"])
    new_rows.extend(buckets["liab_current"])
    if subtotals["流動負債合計"]: new_rows.append(subtotals["流動負債合計"])

    if headers["固定負債"]: new_rows.append(headers["固定負債"])
    new_rows.extend(buckets["liab_noncurrent"])
    if subtotals["固定負債合計"]: new_rows.append(subtotals["固定負債合計"])

    if majors["負債の部合計"]: new_rows.append(majors["負債の部合計"])

    # --- 純資産 ---
    if headers["株主資本"]: new_rows.append(headers["株主資本"])
    new_rows.extend(buckets["equity"])
    if subtotals["株主資本合計"]: new_rows.append(subtotals["株主資本合計"])
    if majors["純資産の部合計"]: new_rows.append(majors["純資産の部合計"])

    # --- 不明（other）は**最終合計の直前**に集約（ここまでで迷子は基本的に出ない想定）
    other_sorted = sorted(buckets["other"], key=lambda x: x["_orig_idx"])
    new_rows.extend(other_sorted)

    # --- 最終合計は末尾 ---
    if majors["負債・純資産の部合計"]:
        new_rows.append(majors["負債・純資産の部合計"])

    # 内部キー削除
    for r in new_rows:
        r.pop("_orig_idx", None)

    return new_rows

# ========= マージ処理 =========
def unify_and_index_current(baseline_current: list) -> tuple[list, dict]:
    rows = []
    index_by_key = {}
    for item in baseline_current:
        name = normalize_name(item.get("勘定科目"))
        klass = normalize_classification(name, item.get("分類"))
        typ = item.get("type", "BS")
        out = to_output_row(name, klass, typ)
        amt, pno = extract_amount_page_with_order(item, CURRENT_LABEL_ORDER)
        out["今期"] = {"金額": amt, "page_no": pno}
        if name not in index_by_key:
            index_by_key[name] = len(rows)
            rows.append(out)
    return rows, index_by_key

def safe_insert_new_item(rows: list, new_row: dict) -> None:
    # 今期に無い科目の新規挿入：一旦追加→後段の canonical_rebuild で最終整列するため、ここでは末尾追加のみ
    rows.append(new_row)

def merge_period(rows: list, index_by_key: dict, items: list, target_col: str, label_order: List[str]) -> tuple[list, dict]:
    for item in items:
        name = normalize_name(item.get("勘定科目"))
        amt, pno = extract_amount_page_with_order(item, label_order)
        klass = normalize_classification(name, item.get("分類"))
        typ = item.get("type", "BS")
        if name in index_by_key:
            idx = index_by_key[name]
            rows[idx][target_col] = {"金額": amt, "page_no": pno}  # 列またぎ禁止
            if not rows[idx].get("分類"):
                rows[idx]["分類"] = klass if klass else ACCOUNT_CLASS_HINT.get(name, "")
            if not rows[idx].get("type"):
                rows[idx]["type"] = typ
        else:
            new_row = to_output_row(name, klass, typ)
            new_row[target_col] = {"金額": amt, "page_no": pno}
            safe_insert_new_item(rows, new_row)
            index_by_key = {r["勘定科目"]: i for i, r in enumerate(rows)}
    return rows, index_by_key

def enforce_final_row(rows: list) -> list:
    idx = next((i for i, r in enumerate(rows) if r["勘定科目"] == FINAL_TOTAL_KEY), None)
    if idx is None:
        rows.append(to_output_row(FINAL_TOTAL_KEY, "負債純資産合計", "BS"))
    else:
        rows.append(rows.pop(idx))
    return rows

def validate_amount_equality(rows: list) -> dict:
    def get_amount(key, prd):
        for r in rows:
            if r["勘定科目"] == key:
                return r.get(prd, {}).get("金額")
        return None
    res = {}
    for prd in ("今期", "前期", "前々期"):
        a = get_amount("資産の部合計", prd)
        b = get_amount(FINAL_TOTAL_KEY, prd)
        if is_blank(a) or is_blank(b):
            res[f"{prd}_資産合計_vs_負債純資産合計"] = "N/A"
        else:
            res[f"{prd}_資産合計_vs_負債純資産合計"] = "OK" if a == b else "NG"
    return res
def read_text_with_fallbacks(path: str) -> str:
    """
    path を最優先。その後、/content, /mnt/data, 先頭スラッシュ補完・大小文字ゆらぎも探索して
    最初に読めたファイルの中身（UTF-8テキスト）を返す。見つからなければ "" を返す。
    """
    if not isinstance(path, str) or not path.strip():
        return ""

    candidates = [path]

    # 先頭スラッシュ補完
    if not path.startswith("/"):
        candidates.append("/" + path)

    # /content 互換
    if path.startswith("content/"):
        candidates.append("/content/" + path[len("content/"):])

    # /mnt/data 互換 & 大文字小文字差（BS_*.json の揺れに追随）
    base = os.path.basename(path)
    names = {base}
    if "_bs.json" in base:
        names.add(base.replace("_bs.json", "_BS.json"))
    if "_BS.json" in base:
        names.add(base.replace("_BS.json", "_bs.json"))
    if base.lower().endswith(".json"):
        names.add(base.lower())
        names.add(base.upper())

    for dn in ["/mnt/data", "/content", "content", "/"]:
        for nm in names:
            candidates.append(os.path.join(dn, nm))

    # 重複排除
    seen = set()
    uniq_candidates = []
    for p in candidates:
        if p not in seen:
            uniq_candidates.append(p)
            seen.add(p)

    for p in uniq_candidates:
        try:
            if os.path.exists(p) and os.path.isfile(p):
                with open(p, "r", encoding="utf-8") as f:
                    return f.read()
        except Exception:
            continue

    return ""
# ---------- 入力互換ラッパー ----------
#分析エンジンはこのメソッドを使います
def sort_bs_from_input_2(input_obj: Any) -> List[dict]:
    # json 文字列なら parse
    if isinstance(input_obj, str):
        try:
            parsed = json.loads(input_obj)
            return sort_bs_from_input_2(parsed)
        except Exception:
            return []
    return input_obj