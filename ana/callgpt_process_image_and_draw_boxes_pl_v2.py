# -*- coding: utf-8 -*-
"""
PL用：勘定科目エリア抽出 + 売上原価（COGS）セクションの白塗り（2列以上ある行の2列目以降のみ）

このモジュールは、ユーザー提示の「新コード（v2.5ベース + COGS白塗り改良版）」を
callgpt.py から import して使えるように整理したものです。

変更点（移植時の整理）:
- Colab 依存（cv2_imshow / glob / 直接実行ブロック）を削除
- 入出力は「ファイル読み込み → 加工 → ファイル保存」
- 新コードに含まれていた未定義変数（DEBUG_IMAGE_SCALE_WIDTH）を排除
- 認証鍵は key_path 引数 / GOOGLE_APPLICATION_CREDENTIALS 環境変数を使用可能

公開API:
    process_image_and_draw_boxes_pl(
        input_image_path: str,
        output_image_path: str,
        key_path: str | None = None,
        debug_print: bool = False,
        debug_draw: bool = False,
    ) -> dict

返り値 dict には、検出した売上原価セクション範囲などを含みます（必要なら利用）。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

try:
    from google.cloud import vision
    from google.cloud.vision_v1 import types
except Exception as e:  # pragma: no cover
    # 実行環境に google-cloud-vision が入っていない場合でも import 自体は通したいケースがあるため
    vision = None  # type: ignore
    types = None  # type: ignore
    _IMPORT_ERR = e
else:
    _IMPORT_ERR = None


# ========= 設定（新コード準拠：必要に応じて関数引数で上書き可） =========

# 「売上原価セクション」検出失敗時は白塗りをスキップする（安全側）
SKIP_IF_SECTION_NOT_FOUND_DEFAULT = True

# 金額行候補抽出のY許容（大きすぎると別行の金額が混入する）
AMOUNT_ROW_Y_PAD_DEFAULT = 14

# 同一行（同一金額行）とみなすYクラスタ閾値
Y_GAP_THRESHOLD_DEFAULT = 10

# 列クラスタのX間隔閾値（列間の距離に対し十分小さく）
X_GAP_THRESHOLD_DEFAULT = 90

# bbox余白
ERASE_PAD_X_DEFAULT = 12
ERASE_PAD_Y_DEFAULT = 7


# ========= OCR 座標処理 =========
class Bound:
    def __init__(self, vertices):
        xs = [v.x for v in vertices]
        ys = [v.y for v in vertices]
        self._left = min(xs)
        self._right = max(xs)
        self._top = min(ys)
        self._bottom = max(ys)

    def get_left(self) -> int: return int(self._left)
    def get_right(self) -> int: return int(self._right)
    def get_top(self) -> int: return int(self._top)
    def get_bottom(self) -> int: return int(self._bottom)


# ========= 判定関数 =========
def is_numerical_text(text: str) -> bool:
    if not text.strip():
        return False
    if any(c.isdigit() or c in "０１２３４５６７８９" for c in text):
        return True
    if text in [",", ".", "(", ")", "△", "-", "+", "¥", "円", " "]:
        return True
    return False


def has_any_digit(text: str) -> bool:
    if not text:
        return False
    for c in text:
        if c.isdigit() or c in "０１２３４５６７８９":
            return True
    return False


EXCLUDE_TOKENS = ["TKC", "tkc", "T", "K", "C", "一", "-", "…", "・", "|", "/", ":", "(", ")"]


def is_account_text(text: str) -> bool:
    if not text.strip():
        return False
    if is_numerical_text(text):
        return False
    if text.upper() in EXCLUDE_TOKENS:
        return False
    return True


# ========= ヘッダー行判定 =========
DATE_HEADER_PATTERN = re.compile(
    r"(平成|令和|昭和|大正|明治)?\d{1,4}[元]?年(\d{1,2}月)?(\d{1,2}日)?|\d{4}[/-]\d{1,2}[/-]\d{1,2}|"
    r"(計算書|報告書|決算書|連結|貸借対照表|損益計算書|自|至|期間|科目|金額|費目|円|千円)"
)


def is_header_row(row_text: str) -> bool:
    return DATE_HEADER_PATTERN.search(row_text) is not None


# ========= 正規化 =========
def normalize_jp_text(s: str) -> str:
    if s is None:
        return ""
    s = s.replace("\u3000", " ")
    s = re.sub(r"[\s・\.\,\:\;\|\(\)\[\]\{\}\/\\]+", "", s)
    s = s.replace("”", "").replace('"', "").replace("'", "")
    return s


# ========= 売上原価セクション検出用の正規化（Ⅱ等を除去） =========
_ROMAN_CHARS = "ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ"


def normalize_for_section_detection(s: str) -> str:
    """
    売上原価/売上総利益検出用：
    - 空白・記号除去
    - ローマ数字（ⅠⅡ…）除去
    - ASCIIローマ（I,V,X 等）除去
    - 数字除去（見出しの区分番号混入を無視）
    """
    s = normalize_jp_text(s)

    for ch in _ROMAN_CHARS:
        s = s.replace(ch, "")

    s = re.sub(r"[IVX]+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"[0-9０-９]+", "", s)

    s = (
        s.replace("△", "")
        .replace("▲", "")
        .replace("‐", "")
        .replace("―", "")
        .replace("−", "")
        .replace("-", "")
    )
    return s


def contains_all_chars(t: str, chars: Sequence[str]) -> bool:
    for ch in chars:
        if ch not in t:
            return False
    return True


def is_cogs_heading_norm(t_norm: str) -> bool:
    return contains_all_chars(t_norm, ["売", "上", "原", "価"])


def is_gross_profit_or_loss_heading_norm(t_norm: str) -> bool:
    if not (("売" in t_norm) and ("上" in t_norm) and ("総" in t_norm)):
        return False

    # 利益側
    if ("利" in t_norm) and ("益" in t_norm):
        return True
    if "売上総利" in t_norm:
        return True

    # 損失側
    if ("損" in t_norm) and ("失" in t_norm):
        return True
    if "売上総損" in t_norm:
        return True

    return False


def detect_cogs_section_range_from_all_rows(
    temp_all_rows: List[Dict[str, Any]],
    img_height: int,
) -> Optional[Tuple[int, int]]:
    """
    新コード準拠：売上原価セクション範囲

    start_y 優先順位:
      1) 売上原価 見出し
      2) 期首棚卸系（期首＋棚卸資産語）
    end_y 優先順位:
      1) 売上総利益 / 売上総損失
      2) 期末棚卸系（期末＋棚卸資産語）※下から最後
    """
    cogs_rows: List[Dict[str, Any]] = []
    gpgl_rows: List[Dict[str, Any]] = []
    kisyu_rows: List[Dict[str, Any]] = []
    kimatsu_rows: List[Dict[str, Any]] = []

    for r in temp_all_rows:
        raw = r.get("text", "")
        if not raw.strip():
            continue
        if is_header_row(raw):
            continue

        t_norm = normalize_for_section_detection(raw)
        if not t_norm:
            continue

        if is_cogs_heading_norm(t_norm):
            cogs_rows.append(r)
        if is_gross_profit_or_loss_heading_norm(t_norm):
            gpgl_rows.append(r)

        has_inventory = any(ch in t_norm for ch in ["棚", "卸", "商", "品", "材", "料", "製", "高"])
        if has_inventory:
            if ("期首" in t_norm) or ("期" in t_norm and "首" in t_norm):
                kisyu_rows.append(r)
            if ("期末" in t_norm) or ("期" in t_norm and ("末" in t_norm or "未" in t_norm)):
                kimatsu_rows.append(r)

    start_y: Optional[int] = None
    if cogs_rows:
        r = min(cogs_rows, key=lambda x: x["y_min"])
        start_y = int(r["y_max"] + 4)
    elif kisyu_rows:
        r = min(kisyu_rows, key=lambda x: x["y_min"])
        start_y = int(r["y_min"] - 4)

    end_y: Optional[int] = None
    if gpgl_rows:
        r = min(gpgl_rows, key=lambda x: x["y_min"])
        end_y = int(r["y_min"] - 4)
    elif kimatsu_rows:
        r = max(kimatsu_rows, key=lambda x: x["y_max"])
        end_y = int(r["y_max"] + 4)

    if start_y is None or end_y is None:
        return None

    start_y = max(0, start_y)
    end_y = min(img_height - 1, end_y)
    if start_y >= end_y:
        return None

    return (start_y, end_y)


# ========= 期首/期末/棚卸関連の除外 =========
def is_forbidden_inventory_row(account_row_text: str) -> bool:
    """
    期首/期末/棚卸に関係する科目は全て除外（白塗りしない）
    表記ゆれ・OCR崩れを広めに許容（新コード準拠）
    """
    t = normalize_jp_text(account_row_text)
    if not t:
        return False

    inv_markers = ["棚", "卸", "商", "品", "材", "料", "製", "品", "高"]
    has_inv = any(ch in t for ch in inv_markers)
    if not has_inv:
        return False

    if "期首" in t or "期末" in t or "期未" in t:
        return True
    if ("期" in t and "首" in t) or ("期" in t and "末" in t) or ("期" in t and "未" in t):
        return True

    return False


# ========= Vision API OCR =========
def visionAPI(fname: str, key_path: Optional[str], debug_print: bool) -> List[Dict[str, Any]]:
    if _IMPORT_ERR is not None or vision is None or types is None:
        raise ImportError(
            "google-cloud-vision がインストールされていません。"
            " pip install google-cloud-vision などで導入してください。"
        ) from _IMPORT_ERR

    if key_path:
        if not os.path.exists(key_path):
            raise FileNotFoundError(f"Vision API key file not found: {key_path}")
        client = vision.ImageAnnotatorClient.from_service_account_json(key_path)
    else:
        # GOOGLE_APPLICATION_CREDENTIALS が設定されていれば自動利用
        client = vision.ImageAnnotatorClient()

    img = cv2.imread(fname)
    if img is None:
        raise FileNotFoundError(f"画像を読み込めません: {fname}")

    ok, content = cv2.imencode(".jpg", img)
    if not ok:
        raise RuntimeError("cv2.imencode に失敗しました")

    image = types.Image(content=content.tobytes())
    response = client.document_text_detection(
        image=image,
        image_context={"language_hints": ["ja-t-i0-handwrit"]},
    )

    characters: List[Dict[str, Any]] = []
    document = response.full_text_annotation

    # ここで error が返ってくる場合もあるが、環境によって形が違うので最低限のログだけ出す
    if debug_print and getattr(response, "error", None) and getattr(response.error, "message", ""):
        print("Vision API error:", response.error.message)

    for page in document.pages:
        for block in page.blocks:
            for paragraph in block.paragraphs:
                for word in paragraph.words:
                    for symbol in word.symbols:
                        b = Bound(symbol.bounding_box.vertices)
                        characters.append(
                            {
                                "text": symbol.text,
                                "left": b.get_left(),
                                "top": b.get_top(),
                                "right": b.get_right(),
                                "bottom": b.get_bottom(),
                            }
                        )
    return characters


# ========= 勘定科目エリア抽出（v2.5ベース：新コード準拠のrow_text追加） =========
def identify_account_area_and_lines_final(
    characters: List[Dict[str, Any]],
    original_img_path: str,
) -> Tuple[None, List[Tuple[int, int, int, int, List[Dict[str, Any]], str]], List[Dict[str, Any]]]:
    img = cv2.imread(original_img_path)
    if img is None:
        return None, [], []

    width = img.shape[1]

    # 1) 全文字から行をまとめ、ヘッダー判定に利用
    all_chars_sorted = sorted(characters, key=lambda x: x["top"])
    temp_all_rows: List[Dict[str, Any]] = []

    if all_chars_sorted:
        current_row_chars: List[Dict[str, Any]] = []
        for char in all_chars_sorted:
            if not current_row_chars:
                current_row_chars.append(char)
            else:
                if char["top"] - current_row_chars[-1]["top"] < 7:
                    current_row_chars.append(char)
                else:
                    row_text = "".join(c["text"] for c in current_row_chars)
                    y_min = min(c["top"] for c in current_row_chars)
                    y_max = max(c["bottom"] for c in current_row_chars)
                    temp_all_rows.append({"text": row_text, "y_min": y_min, "y_max": y_max, "chars": current_row_chars})
                    current_row_chars = [char]

        if current_row_chars:
            row_text = "".join(c["text"] for c in current_row_chars)
            y_min = min(c["top"] for c in current_row_chars)
            y_max = max(c["bottom"] for c in current_row_chars)
            temp_all_rows.append({"text": row_text, "y_min": y_min, "y_max": y_max, "chars": current_row_chars})

    # 2) ヘッダーではない行にある数値のみ抽出
    non_header_num_bounds: List[Tuple[int, int, int, int]] = []
    for temp_row in temp_all_rows:
        if is_header_row(temp_row["text"]):
            continue
        for char in temp_row["chars"]:
            if is_numerical_text(char["text"]):
                non_header_num_bounds.append((char["left"], char["top"], char["right"], char["bottom"]))

    # 3) 勘定科目エリアの最大右端を決定
    num_x_min_list_for_x_end = [b[0] for b in non_header_num_bounds if b[0] > width // 2]
    max_account_x_end = width * 0.6
    if num_x_min_list_for_x_end:
        min_num_x = min(num_x_min_list_for_x_end)
        max_account_x_end = min_num_x - 2
        max_account_x_end = max(width * 0.4, max_account_x_end)

    # 4) 勘定科目候補抽出
    account_chars = [c for c in characters if is_account_text(c["text"]) and c["right"] < max_account_x_end + 15]
    if not account_chars:
        return None, [], temp_all_rows

    # 5) 勘定科目候補で構成される行データ作成（ヘッダー除外）
    account_chars_sorted = sorted(account_chars, key=lambda x: x["top"])
    rows_data: List[Dict[str, Any]] = []

    if account_chars_sorted:
        current_row_chars = []
        for char in account_chars_sorted:
            if not current_row_chars:
                current_row_chars.append(char)
            else:
                if char["top"] - current_row_chars[-1]["top"] < 7:
                    current_row_chars.append(char)
                else:
                    y_min = min(c["top"] for c in current_row_chars)
                    y_max = max(c["bottom"] for c in current_row_chars)
                    row_text = "".join(c["text"] for c in current_row_chars)
                    if not is_header_row(row_text):
                        rows_data.append({"y_start": y_min, "y_end": y_max, "chars": current_row_chars, "text": row_text})
                    current_row_chars = [char]

        if current_row_chars:
            y_min = min(c["top"] for c in current_row_chars)
            y_max = max(c["bottom"] for c in current_row_chars)
            row_text = "".join(c["text"] for c in current_row_chars)
            if not is_header_row(row_text):
                rows_data.append({"y_start": y_min, "y_end": y_max, "chars": current_row_chars, "text": row_text})

    # 6) 数値がある行だけ採用し、行ごとの左端・右端決定
    filtered_rows: List[Tuple[int, int, int, int, List[Dict[str, Any]], str]] = []
    last_y_end = -1

    num_x_min_list_for_amount = [b[0] for b in non_header_num_bounds if b[0] > width // 3]
    num_area_start_x = width // 2
    if num_x_min_list_for_amount:
        num_area_start_x = min(num_x_min_list_for_amount) - 10

    Y_PADDING = 2

    for row in rows_data:
        y1_raw, y2_raw = row["y_start"], row["y_end"]
        row_chars = row["chars"]
        has_amount = False

        for bx1, by1, bx2, by2 in non_header_num_bounds:
            if bx1 >= num_area_start_x + 5:
                if min(by2, y2_raw + Y_PADDING) - max(by1, y1_raw - Y_PADDING) > 0:
                    has_amount = True
                    break

        if has_amount:
            row_x_start_raw = min(c["left"] for c in row_chars)
            x_start_row = max(0, row_x_start_raw - 20)

            row_x_end_raw = max(c["right"] for c in row_chars)
            x_end_row = min(row_x_end_raw + 5, int(max_account_x_end) + 10)

            y_start = max(y1_raw - Y_PADDING, last_y_end + 1)
            y_end = y2_raw + Y_PADDING

            if y_start < y_end:
                filtered_rows.append((int(x_start_row), int(y_start), int(y_end), int(x_end_row), row_chars, row["text"]))
                last_y_end = y_end

    return None, filtered_rows, temp_all_rows


# ========= 数値トークン（非ヘッダー） =========
def build_non_header_num_tokens(characters: List[Dict[str, Any]], temp_all_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    header_y_ranges: List[Tuple[int, int]] = []
    for r in temp_all_rows:
        if is_header_row(r["text"]):
            header_y_ranges.append((int(r["y_min"]), int(r["y_max"])))

    def in_header_y(yc: float) -> bool:
        for y1, y2 in header_y_ranges:
            if y1 <= yc <= y2:
                return True
        return False

    tokens: List[Dict[str, Any]] = []
    for c in characters:
        if not is_numerical_text(c["text"]):
            continue
        yc = (c["top"] + c["bottom"]) / 2.0
        if in_header_y(yc):
            continue
        tokens.append(
            {
                "text": c["text"],
                "left": c["left"],
                "right": c["right"],
                "top": c["top"],
                "bottom": c["bottom"],
                "x_center": (c["left"] + c["right"]) / 2.0,
                "y_center": (c["top"] + c["bottom"]) / 2.0,
            }
        )
    return tokens


# ========= 列推定（Xクラスタ） =========
def cluster_digit_tokens_by_x(num_tokens: List[Dict[str, Any]], x_gap_threshold: int) -> List[List[Dict[str, Any]]]:
    digit_tokens = [t for t in num_tokens if has_any_digit(t["text"])]
    if not digit_tokens:
        return []

    tokens_sorted = sorted(digit_tokens, key=lambda t: t["x_center"])
    clusters: List[List[Dict[str, Any]]] = []
    cur = [tokens_sorted[0]]

    for t in tokens_sorted[1:]:
        if abs(t["x_center"] - cur[-1]["x_center"]) <= x_gap_threshold:
            cur.append(t)
        else:
            clusters.append(cur)
            cur = [t]
    clusters.append(cur)

    def cx(cl: List[Dict[str, Any]]) -> float:
        return float(np.median([x["x_center"] for x in cl]))

    clusters = sorted(clusters, key=cx)
    return clusters


# ========= 同一行推定（Yクラスタ） =========
def cluster_tokens_by_y(num_tokens: List[Dict[str, Any]], y_gap_threshold: int) -> List[List[Dict[str, Any]]]:
    if not num_tokens:
        return []
    tokens_sorted = sorted(num_tokens, key=lambda t: t["y_center"])
    clusters: List[List[Dict[str, Any]]] = []
    cur = [tokens_sorted[0]]

    for t in tokens_sorted[1:]:
        if abs(t["y_center"] - cur[-1]["y_center"]) <= y_gap_threshold:
            cur.append(t)
        else:
            clusters.append(cur)
            cur = [t]
    clusters.append(cur)
    return clusters


def select_best_y_cluster(y_clusters: List[List[Dict[str, Any]]], target_y_center: float) -> Optional[List[Dict[str, Any]]]:
    """
    target_y_center に最も近く、かつ digit数が多いクラスタを選ぶ
    """
    if not y_clusters:
        return None

    best: Optional[List[Dict[str, Any]]] = None
    best_score: Optional[Tuple[float, int]] = None

    for cl in y_clusters:
        if not cl:
            continue
        med_y = float(np.median([t["y_center"] for t in cl]))
        digit_count = sum(1 for t in cl if has_any_digit(t["text"]))
        score = (abs(med_y - target_y_center), -digit_count)

        if best is None or best_score is None or score < best_score:
            best = cl
            best_score = score

    return best


# ========= bbox生成（指定列クラスタを白塗り） =========
def build_erase_bbox_for_cluster(
    cluster_digit_tokens: List[Dict[str, Any]],
    row_all_num_tokens: List[Dict[str, Any]],
    pad_x: int,
    pad_y: int,
    join_margin_x: int = 26,
) -> Optional[Tuple[int, int, int, int]]:
    if not cluster_digit_tokens:
        return None

    x1 = min(t["left"] for t in cluster_digit_tokens)
    x2 = max(t["right"] for t in cluster_digit_tokens)
    y1 = min(t["top"] for t in cluster_digit_tokens)
    y2 = max(t["bottom"] for t in cluster_digit_tokens)

    for t in row_all_num_tokens:
        if min(t["bottom"], y2) - max(t["top"], y1) <= 0:
            continue
        if (t["right"] >= x1 - join_margin_x) and (t["left"] <= x2 + join_margin_x):
            x1 = min(x1, t["left"])
            x2 = max(x2, t["right"])
            y1 = min(y1, t["top"])
            y2 = max(y2, t["bottom"])

    x1 = int(x1 - pad_x)
    x2 = int(x2 + pad_x)
    y1 = int(y1 - pad_y)
    y2 = int(y2 + pad_y)
    return (x1, y1, x2, y2)


# ========= 白塗り処理（売上原価セクション内：2列目以降） =========
def erase_second_and_later_amount_columns_in_cogs(
    img: np.ndarray,
    characters: List[Dict[str, Any]],
    rows_with_x_end: List[Tuple[int, int, int, int, List[Dict[str, Any]], str]],
    temp_all_rows: List[Dict[str, Any]],
    *,
    skip_if_section_not_found: bool,
    amount_row_y_pad: int,
    y_gap_threshold: int,
    x_gap_threshold: int,
    erase_pad_x: int,
    erase_pad_y: int,
    debug_print: bool,
    debug_draw: bool,
) -> Tuple[np.ndarray, Optional[Tuple[int, int]]]:
    h, w = img.shape[:2]

    sec = detect_cogs_section_range_from_all_rows(temp_all_rows, h)
    if sec is None:
        if debug_print:
            print("警告: 売上原価セクション範囲の検出に失敗しました（白塗りはスキップ）。")
        if skip_if_section_not_found:
            return img, None
        return img, None

    start_y, end_y = sec
    if debug_print:
        print(f"売上原価セクション範囲: start_y={start_y}, end_y={end_y}")

    num_tokens_all = build_non_header_num_tokens(characters, temp_all_rows)

    out = img.copy()

    # デバッグ線（セクション上下）
    if debug_draw:
        cv2.line(out, (0, start_y), (w - 1, start_y), (255, 0, 0), 2)
        cv2.line(out, (0, end_y), (w - 1, end_y), (255, 0, 0), 2)

    for x_start, y1, y2, x_end, row_chars, row_text in rows_with_x_end:
        row_center_y = (y1 + y2) / 2.0

        # 売上原価セクション内のみ
        if not (start_y <= row_center_y <= end_y):
            continue

        # 在庫関連は必ず除外
        if is_forbidden_inventory_row(row_text):
            continue

        # 行の近傍から金額トークン候補抽出（Yで広めに拾ってから、Yクラスタで確定）
        y_min = y1 - amount_row_y_pad
        y_max = y2 + amount_row_y_pad

        row_amount_tokens: List[Dict[str, Any]] = []
        for t in num_tokens_all:
            if t["x_center"] < w * 0.35:
                continue
            if y_min <= t["y_center"] <= y_max:
                row_amount_tokens.append(t)

        if not row_amount_tokens:
            continue

        y_clusters = cluster_tokens_by_y(row_amount_tokens, y_gap_threshold=y_gap_threshold)
        selected_row_tokens = select_best_y_cluster(y_clusters, target_y_center=row_center_y)
        if not selected_row_tokens:
            continue

        x_clusters = cluster_digit_tokens_by_x(selected_row_tokens, x_gap_threshold=x_gap_threshold)
        if len(x_clusters) < 2:
            continue

        erase_bboxes: List[Tuple[int, int, int, int]] = []
        for cl in x_clusters[1:]:
            bbox = build_erase_bbox_for_cluster(
                cl,
                selected_row_tokens,
                pad_x=erase_pad_x,
                pad_y=erase_pad_y,
                join_margin_x=26,
            )
            if bbox is None:
                continue
            ex1, ey1, ex2, ey2 = bbox
            ex1 = max(0, min(w - 1, ex1))
            ex2 = max(0, min(w - 1, ex2))
            ey1 = max(0, min(h - 1, ey1))
            ey2 = max(0, min(h - 1, ey2))
            if ex1 < ex2 and ey1 < ey2:
                erase_bboxes.append((ex1, ey1, ex2, ey2))

        if not erase_bboxes:
            continue

        for (ex1, ey1, ex2, ey2) in erase_bboxes:
            cv2.rectangle(out, (ex1, ey1), (ex2, ey2), (255, 255, 255), thickness=-1)

        if debug_draw:
            for t in selected_row_tokens:
                cv2.rectangle(
                    out,
                    (int(t["left"]), int(t["top"])),
                    (int(t["right"]), int(t["bottom"])),
                    (0, 0, 255),
                    1,
                )
            for (ex1, ey1, ex2, ey2) in erase_bboxes:
                cv2.rectangle(out, (ex1, ey1), (ex2, ey2), (0, 165, 255), 2)

        if debug_print:
            # 必要ならここを条件付きにしてもOK。新コードは「リ/収」を例にしていた。
            row_account_text = "".join([c["text"] for c in sorted(row_chars, key=lambda z: (z["left"], z["top"]))])
            row_account_norm = normalize_jp_text(row_account_text)
            if ("リ" in row_account_norm) or ("収" in row_account_norm):
                print("---- DEBUG ERASE ROW ----")
                print(f"row_account_text(raw) : {row_account_text}")
                print(f"row_account_text(norm): {row_account_norm}")
                print(f"row_y1={y1} row_y2={y2} row_center_y={row_center_y}")
                print(f"selected_row_tokens={len(selected_row_tokens)} x_clusters={len(x_clusters)} erase_boxes={len(erase_bboxes)}")
                for i, cl in enumerate(x_clusters):
                    cx = float(np.median([t2['x_center'] for t2 in cl])) if cl else -1
                    span = (min(t2['left'] for t2 in cl), max(t2['right'] for t2 in cl)) if cl else (-1, -1)
                    digits = "".join([t2['text'] for t2 in sorted(cl, key=lambda z: z['left'])])
                    print(f"  col[{i}] center_x={cx:.1f} span={span} text='{digits}'")
                print("-------------------------")

    return out, (start_y, end_y)


# ========= 緑枠を描画 =========
def draw_green_boxes(img: np.ndarray, rows_with_x_end: List[Tuple[int, int, int, int, List[Dict[str, Any]], str]]) -> np.ndarray:
    out = img.copy()
    for x_start, y1, y2, x_end, _row_chars, _row_text in rows_with_x_end:
        x_start = max(0, int(x_start))
        y1 = max(0, int(y1))
        x_end = min(out.shape[1] - 1, int(x_end))
        y2 = min(out.shape[0] - 1, int(y2))
        if x_start < x_end and y1 < y2:
            cv2.rectangle(out, (x_start, y1), (x_end, y2), (0, 255, 0), 2)
    return out


# ========= 公開API =========
def process_image_and_draw_boxes_pl(
    input_image_path: str,
    output_image_path: str,
    key_path: Optional[str] = None,
    debug_print: bool = False,
    debug_draw: bool = False,
    *,
    skip_if_section_not_found: bool = SKIP_IF_SECTION_NOT_FOUND_DEFAULT,
    amount_row_y_pad: int = AMOUNT_ROW_Y_PAD_DEFAULT,
    y_gap_threshold: int = Y_GAP_THRESHOLD_DEFAULT,
    x_gap_threshold: int = X_GAP_THRESHOLD_DEFAULT,
    erase_pad_x: int = ERASE_PAD_X_DEFAULT,
    erase_pad_y: int = ERASE_PAD_Y_DEFAULT,
) -> Dict[str, Any]:
    """
    input_image_path をOCRして、
      1) 売上原価セクション内の「金額が2列以上ある行」について、2列目以降の金額列を白塗り
      2) 勘定科目（左欄）の行範囲に緑枠を描画
    した画像を output_image_path に保存します。

    戻り値:
      {
        "ok": bool,
        "output_image_path": str,
        "section_range": (start_y, end_y) | None,
        "rows_count": int,
      }
    """
    if not input_image_path:
        raise ValueError("input_image_path is empty")
    if not output_image_path:
        raise ValueError("output_image_path is empty")

    # key_path が未指定なら環境変数も尊重（callgpt側で渡していなくても動くように）
    if not key_path:
        key_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

    if not os.path.exists(input_image_path):
        raise FileNotFoundError(f"input image not found: {input_image_path}")

    characters = visionAPI(input_image_path, key_path=key_path, debug_print=debug_print)

    img = cv2.imread(input_image_path)
    if img is None:
        raise FileNotFoundError(f"画像を読み込めません: {input_image_path}")

    _area, rows_with_x_end, temp_all_rows = identify_account_area_and_lines_final(characters, input_image_path)
    if not rows_with_x_end:
        # 緑枠も描けないので、そのままコピー保存して返す
        cv2.imwrite(output_image_path, img)
        return {"ok": False, "output_image_path": output_image_path, "section_range": None, "rows_count": 0}

    img_erased, sec = erase_second_and_later_amount_columns_in_cogs(
        img,
        characters,
        rows_with_x_end,
        temp_all_rows,
        skip_if_section_not_found=skip_if_section_not_found,
        amount_row_y_pad=amount_row_y_pad,
        y_gap_threshold=y_gap_threshold,
        x_gap_threshold=x_gap_threshold,
        erase_pad_x=erase_pad_x,
        erase_pad_y=erase_pad_y,
        debug_print=debug_print,
        debug_draw=debug_draw,
    )

    img_out = draw_green_boxes(img_erased, rows_with_x_end)

    # 出力先ディレクトリの作成（必要なら）
    out_dir = os.path.dirname(output_image_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    ok = cv2.imwrite(output_image_path, img_out)
    if not ok:
        raise RuntimeError(f"cv2.imwrite failed: {output_image_path}")

    return {
        "ok": True,
        "output_image_path": output_image_path,
        "section_range": sec,
        "rows_count": len(rows_with_x_end),
    }
