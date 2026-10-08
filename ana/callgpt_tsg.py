# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
import time
from typing import Optional, List, Dict, Tuple, Any

import cv2
import numpy as np

from google.cloud import vision
from google.protobuf.json_format import MessageToDict

from azure.cognitiveservices.vision.computervision import ComputerVisionClient
from azure.cognitiveservices.vision.computervision.models import OperationStatusCodes
from msrest.authentication import CognitiveServicesCredentials

# =====================================================================================
# ★ 外部から import 後に tsg_ocr_boxer.debug_mode = True でONできます
# =====================================================================================
debug_mode = False

# =====================================================================================
# ★ Azure 設定（固定）
#   - エンドポイントはご指定の通り固定
#   - キーはコードに直書きせず、環境変数から読む（安全）
# =====================================================================================
AZURE_ENDPOINT = "https://zlite.cognitiveservices.azure.com/"
AZURE_KEY_ENVNAME = "AZURE_API_KEY"  # 例: export AZURE_API_KEY="xxxxx"

# もし「どうしてもコードにキーを埋めたい」場合は以下に入れる（非推奨）
# ※ここに実キーを書かないでください。漏洩します。
AZURE_KEY_FALLBACK = ""  # [pmj-ana] 直書きのキーを削除  # 例: "xxxxxxxxxxxxxxxxxxxxxxxx"（非推奨）


# =====================================================================================
# 解析パラメータ
# =====================================================================================
# 金額列判定を x2(右端) ベースにする（右寄せ帳票に強い）
WALL_BASE = "x2"   # "x2" 推奨

# 0円（またはOCRで金額が取れない）行の×を抑止
SUPPRESS_X_FOR_SUBJECT_ONLY_ROWS = True
MIN_SUBJECT_CHARS_FOR_SUPPRESS = 1

# 行構築を「中心距離」で厳格化して、2行混在を分離
ENABLE_STRICT_ROW_BUILD = True
ROW_MERGE_CY_FACTOR = 0.55
ROW_MERGE_MIN_H = 8
ROW_MERGE_MAX_H = 40

# --- デバッグ詳細設定（debug_mode=True時のみ有効） ---
DEBUG_MAX_ROWS_DETAIL = 400
DEBUG_DRAW_DEBUG_GUIDES = True
DEBUG_DRAW_ROW_ID = True
DEBUG_DRAW_FAILED_MARKS = True


def dprint(*args, **kwargs):
    if debug_mode:
        print(*args, **kwargs)


def is_numeric_char(txt: str) -> bool:
    return bool(re.search(r"[0-9０-９,，.\-△▲￥¥]", txt))


def is_bracket_or_empty(txt: str) -> bool:
    return len(re.sub(r"[()（）\[\]\s]", "", txt)) == 0


# --- 【厳守】年号・日付・タイトル判定ロジック ---
def is_date_or_title(elements: List[Dict[str, Any]]) -> bool:
    full_text = "".join([e["text"] for e in elements]).replace(" ", "").replace("　", "")
    patterns = [
        r"令和", r"平成", r"昭和", r"[2\d]\d{3}年", r"\d{1,2}月\d{1,2}日",
        r"現在", r"貸借対照表", r"単位", r"資産の部", r"負債の部", r"純資産の部",
        r"^令$", r"^和$", r"^\(単位"
    ]
    return any(re.search(p, full_text) for p in patterns)


def clip(v: float, lo: int, hi: int) -> int:
    return int(max(lo, min(hi, int(v))))


def _get_azure_key() -> str:
    """
    Azureキー取得（優先：環境変数 → フォールバック定数）
    """
    k = os.getenv(AZURE_KEY_ENVNAME, "").strip()
    if k:
        return k
    if AZURE_KEY_FALLBACK.strip():
        return AZURE_KEY_FALLBACK.strip()
    raise RuntimeError(
        f"Azure key not found. Set env {AZURE_KEY_ENVNAME}.\n"
        f"Example: export {AZURE_KEY_ENVNAME}='YOUR_AZURE_KEY'"
    )


def get_ocr_data(image_path: str, azure_key: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Azure Read API で単語（text付き）を取る => az_els
    Google Vision でbbox（主にy補正用）を取る => v_syms
    """
    client = ComputerVisionClient(AZURE_ENDPOINT, CognitiveServicesCredentials(azure_key))
    with open(image_path, "rb") as s:
        res = client.read_in_stream(s, raw=True)
    op_id = res.headers["Operation-Location"].split("/")[-1]

    while True:
        result = client.get_read_result(op_id)
        if result.status not in ["notStarted", "running"]:
            break
        time.sleep(0.5)

    az_els: List[Dict[str, Any]] = []
    if result.status == OperationStatusCodes.succeeded:
        for p in result.analyze_result.read_results:
            for l in p.lines:
                for w in l.words:
                    b = w.bounding_box
                    xs, ys = [b[0], b[2], b[4], b[6]], [b[1], b[3], b[5], b[7]]
                    x1, x2 = int(min(xs)), int(max(xs))
                    y1, y2 = int(min(ys)), int(max(ys))
                    cy = int(sum(ys) / 4)
                    cx = int(sum(xs) / 4)
                    az_els.append({
                        "text": w.text,
                        "x1": x1, "x2": x2,
                        "y1": y1, "y2": y2,
                        "cy": cy, "cx": cx,
                        "is_num": is_numeric_char(w.text),
                    })

    # Google Vision（service-account-file.json は env GOOGLE_APPLICATION_CREDENTIALS を使う）
    v_client = vision.ImageAnnotatorClient()
    with open(image_path, "rb") as f:
        v_res = MessageToDict(
            v_client.document_text_detection(image=vision.Image(content=f.read()))._pb
        )

    v_syms: List[Dict[str, Any]] = []
    for page in v_res.get("fullTextAnnotation", {}).get("pages", []):
        for block in page.get("blocks", []):
            for para in block.get("paragraphs", []):
                for word in para.get("words", []):
                    vs = word.get("boundingBox", {}).get("vertices", [])
                    if vs:
                        y_vals = [v.get("y", 0) for v in vs]
                        x_vals = [v.get("x", 0) for v in vs]
                        v_syms.append({
                            "x1": min(x_vals), "x2": max(x_vals),
                            "y1": min(y_vals), "y2": max(y_vals),
                            "cy": sum(y_vals) / 4, "cx": sum(x_vals) / 4,
                        })

    return az_els, v_syms


def estimate_typical_height_from_elements(elements: List[Dict[str, Any]]) -> float:
    hs = []
    for e in elements:
        h = int(e["y2"]) - int(e["y1"])
        if h > 0:
            hs.append(h)
    if not hs:
        return 14.0
    return float(np.median(hs))


def build_rows_strict(elements: List[Dict[str, Any]], typical_h: float) -> List[Dict[str, Any]]:
    if not elements:
        return []

    elements.sort(key=lambda e: e["y1"])
    rows: List[Dict[str, Any]] = []

    th = float(typical_h)
    th = max(ROW_MERGE_MIN_H, min(ROW_MERGE_MAX_H, th))
    merge_cy_thr = th * ROW_MERGE_CY_FACTOR

    for e in elements:
        best_i = -1
        best_dist = 1e18
        for i, r in enumerate(rows):
            r_cy = float(r["cy"])
            dist = abs(float(e["cy"]) - r_cy)
            if dist < best_dist:
                best_dist = dist
                best_i = i

        if best_i >= 0 and best_dist <= merge_cy_thr:
            r = rows[best_i]
            r["y1"] = min(r["y1"], e["y1"])
            r["y2"] = max(r["y2"], e["y2"])
            r["els"].append(e)
            r["cy"] = (r["y1"] + r["y2"]) / 2.0
        else:
            rows.append({"y1": e["y1"], "y2": e["y2"], "cy": float(e["cy"]), "els": [e]})

    rows.sort(key=lambda x: x["y1"])
    return rows


def estimate_center_x_from_credit_subjects(
    az_els: List[Dict[str, Any]], default_center: int
) -> Tuple[int, Optional[int], List[Dict[str, Any]]]:
    subject_candidates = [
        e for e in az_els
        if (not e["is_num"])
        and (not is_bracket_or_empty(e["text"]))
        and (e["cx"] > default_center)
    ]
    if not subject_candidates:
        return default_center, None, []
    credit_subject_x1 = min(e["x1"] for e in subject_candidates)
    center_x = int(credit_subject_x1 - 1)
    near = sorted(subject_candidates, key=lambda e: e["x1"])[: min(20, len(subject_candidates))]
    return center_x, credit_subject_x1, near


def estimate_center_x_from_numeric(az_els: List[Dict[str, Any]], default_center: int) -> Tuple[int, Dict[str, Any]]:
    nums = [e for e in az_els if e.get("is_num", False)]
    if len(nums) < 6:
        return default_center, {"reason": "too_few_nums", "n": len(nums)}

    xs = np.array([e["cx"] for e in nums], dtype=np.float32)
    c1, c2 = float(xs.min()), float(xs.max())
    if abs(c2 - c1) < 10:
        return default_center, {"reason": "range_too_small", "min": float(xs.min()), "max": float(xs.max())}

    for _ in range(20):
        d1 = np.abs(xs - c1)
        d2 = np.abs(xs - c2)
        g1 = xs[d1 <= d2]
        g2 = xs[d1 > d2]
        if len(g1) == 0 or len(g2) == 0:
            break
        nc1, nc2 = float(g1.mean()), float(g2.mean())
        if abs(nc1 - c1) < 0.5 and abs(nc2 - c2) < 0.5:
            c1, c2 = nc1, nc2
            break
        c1, c2 = nc1, nc2

    left_c, right_c = (c1, c2) if c1 < c2 else (c2, c1)
    center_x = int((left_c + right_c) / 2.0)
    if center_x < 0 or center_x > default_center * 2:
        return default_center, {"reason": "out_of_range", "center_x": center_x, "default": default_center}

    return center_x, {"reason": "ok", "left_c": left_c, "right_c": right_c, "center_x": center_x}


def pick_center_x(az_els: List[Dict[str, Any]], w: int) -> Tuple[int, int, str, str, Dict[str, Any]]:
    default_center = w // 2
    cx1, credit_subject_x1, near_candidates = estimate_center_x_from_credit_subjects(az_els, default_center)
    cx2, num_info = estimate_center_x_from_numeric(az_els, default_center)

    mode = "credit_subjects"
    reason = "normal"

    if cx1 <= 0 or cx1 >= w:
        mode = "numeric_fallback"
        reason = "credit_subjects_out_of_range"
        return cx2, default_center, mode, reason, {
            "credit_subject_x1": credit_subject_x1,
            "near_candidates": near_candidates,
            "numeric_info": num_info,
            "cx1": cx1, "cx2": cx2
        }

    if abs(cx1 - default_center) > int(w * 0.20):
        if 0 < cx2 < w and abs(cx2 - default_center) < abs(cx1 - default_center):
            mode = "numeric_fallback"
            reason = "numeric_less_deviation"
            return cx2, default_center, mode, reason, {
                "credit_subject_x1": credit_subject_x1,
                "near_candidates": near_candidates,
                "numeric_info": num_info,
                "cx1": cx1, "cx2": cx2
            }
        mode = "credit_subjects"
        reason = "credit_large_deviation_but_used"
        return cx1, default_center, mode, reason, {
            "credit_subject_x1": credit_subject_x1,
            "near_candidates": near_candidates,
            "numeric_info": num_info,
            "cx1": cx1, "cx2": cx2
        }

    return cx1, default_center, mode, reason, {
        "credit_subject_x1": credit_subject_x1,
        "near_candidates": near_candidates,
        "numeric_info": num_info,
        "cx1": cx1, "cx2": cx2
    }


def get_wall_stats(rows: List[Dict[str, Any]]) -> Tuple[Tuple[float, float], int]:
    samples = []
    x2_list = []
    w_list = []

    for r in rows:
        if is_date_or_title(r["els"]):
            continue
        nums = sorted([e for e in r["els"] if e["is_num"]], key=lambda x: x["x1"])
        if nums:
            last_num = nums[-1]
            samples.append((last_num["x1"], last_num["x2"]))
            x2_list.append(last_num["x2"])
            w_list.append(max(1, last_num["x2"] - last_num["x1"]))

    if not samples:
        return (np.nan, np.nan), 0

    if WALL_BASE == "x1":
        wall0 = float(np.median([s[0] for s in samples]))
        wall1 = float(np.median([s[1] for s in samples]))
        return (wall0, wall1), len(samples)

    med_x2 = float(np.median(x2_list))
    med_w = float(np.median(w_list))
    left_span = med_w * 1.6

    wall1 = med_x2
    wall0 = med_x2 - left_span
    return (wall0, wall1), len(samples)


def row_text_summary(r: Dict[str, Any]) -> str:
    els = sorted(r["els"], key=lambda e: e["x1"])
    return "".join([e["text"] for e in els])


def is_subject_only_row_like(side: str, r: Dict[str, Any], wall: Tuple[float, float], center_x: int) -> bool:
    if is_date_or_title(r["els"]):
        return False

    nums = [e for e in r["els"] if e["is_num"]]
    if nums:
        return False

    subs_cand = [e for e in r["els"] if (not e["is_num"]) and (not is_bracket_or_empty(e["text"]))]
    if not subs_cand:
        return False

    subj_text = "".join([e["text"] for e in sorted(subs_cand, key=lambda e: e["x1"])])
    if len(subj_text) < MIN_SUBJECT_CHARS_FOR_SUPPRESS:
        return False

    if np.isnan(wall[0]) or np.isnan(wall[1]):
        return False

    sx1 = min(e["x1"] for e in subs_cand)
    sx2 = max(e["x2"] for e in subs_cand)

    if side == "L":
        return sx2 < (wall[0] - 5)
    else:
        return (sx1 > center_x + 2) and (sx2 < (wall[0] - 5))


def debug_row_decision(side: str, r: Dict[str, Any], wall: Tuple[float, float], center_x: int) -> Dict[str, Any]:
    txt = row_text_summary(r)

    if is_date_or_title(r["els"]):
        return {"row_is_title": True, "row_text": txt}

    if np.isnan(wall[0]):
        return {"row_is_title": False, "row_text": txt, "fail_reason": "wall_is_nan"}

    nums_in_row = [e for e in r["els"] if e["is_num"]]
    hit_num_list = []
    for e in nums_in_row:
        if wall[0] - 10 <= e["x2"] <= wall[1] + 10:
            hit_num_list.append(e)

    if not hit_num_list:
        return {"row_is_title": False, "row_text": txt, "fail_reason": "no_num_near_wall", "num_count": len(nums_in_row)}

    subs = [
        e for e in r["els"]
        if (not e["is_num"])
        and (not is_bracket_or_empty(e["text"]))
        and (
            (e["x2"] < wall[0] - 10) if side == "L"
            else (center_x < e["x1"] < wall[0] - 10)
        )
    ]
    if not subs:
        return {"row_is_title": False, "row_text": txt, "fail_reason": "subs_empty"}

    return {"row_is_title": False, "row_text": txt, "ok_to_draw": True, "subs": subs}


def draw_debug_guides(img, center_x: int, d_wall, c_wall, w: int, h: int):
    cv2.line(img, (center_x, 0), (center_x, h - 1), (0, 0, 255), 2)
    cv2.line(img, (w // 2, 0), (w // 2, h - 1), (0, 0, 255), 1)

    if d_wall is not None and (not np.isnan(d_wall[1])):
        x = int(d_wall[1]); cv2.line(img, (x, 0), (x, h - 1), (255, 0, 0), 2)
    if c_wall is not None and (not np.isnan(c_wall[1])):
        x = int(c_wall[1]); cv2.line(img, (x, 0), (x, h - 1), (0, 255, 0), 2)


def put_text(img, x: int, y: int, s: str, color=(0, 0, 0), scale=0.5, thickness=1):
    cv2.putText(img, str(s), (int(x), int(y)), cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)


def mark_failed_row(img, sx1: int, y1: int, sx2: int, y2: int, color=(0, 0, 255)):
    cv2.line(img, (sx1, y1), (sx2, y2), color, 2)
    cv2.line(img, (sx2, y1), (sx1, y2), color, 2)


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)


def callgpt_tsg(input_file: str, output_file: str, key_path: Optional[str] = None) -> str:
    """
    外部から import して呼べるエントリポイント。

    Args:
        input_file: 入力画像パス（1枚）
        output_file: 出力画像パス（例: /tmp/res_xxx.png）
        key_path: Google VisionのサービスアカウントJSONパス
                 例: "/srv/www/z-lite_backend/internal/service-account-file.json"

    Returns:
        保存した output_file のパス
    """
    if not os.path.exists(input_file):
        raise FileNotFoundError(f"input_file not found: {input_file}")

    # Google Vision の認証（service account json）
    if key_path:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = key_path

    azure_key = _get_azure_key()

    img = cv2.imread(input_file)
    if img is None:
        raise RuntimeError(f"failed to read image: {input_file}")

    h, w = img.shape[:2]
    final_img = img.copy()

    az_els, v_syms = get_ocr_data(input_file, azure_key)

    center_x, default_center, cx_mode, cx_reason, _ = pick_center_x(az_els, w)

    if debug_mode:
        dprint("\n" + "=" * 120)
        dprint(f"[DEBUG] FILE={os.path.basename(input_file)}  W={w} H={h}")
        dprint(f"[DEBUG] center_x={center_x} default_center={default_center} mode={cx_mode}/{cx_reason} WALL_BASE={WALL_BASE}")

    left_elements = [e for e in az_els if e["cx"] < center_x]
    right_elements = [e for e in az_els if e["cx"] >= center_x]

    if ENABLE_STRICT_ROW_BUILD:
        l_typ = estimate_typical_height_from_elements(left_elements)
        r_typ = estimate_typical_height_from_elements(right_elements)
        l_rows = build_rows_strict(left_elements, l_typ)
        r_rows = build_rows_strict(right_elements, r_typ)
    else:
        raise RuntimeError("ENABLE_STRICT_ROW_BUILD=False is not supported in this module version.")

    d_wall, _ = get_wall_stats(l_rows)
    c_wall, _ = get_wall_stats(r_rows)

    if debug_mode and DEBUG_DRAW_DEBUG_GUIDES:
        draw_debug_guides(final_img, center_x, d_wall, c_wall, w, h)
        put_text(final_img, 10, 20, f"center_x={center_x} mode={cx_mode}/{cx_reason} WALL_BASE={WALL_BASE}", (0, 0, 255), 0.6, 2)

    for rows, wall, color, side in [(l_rows, d_wall, (0, 255, 0), "L"), (r_rows, c_wall, (0, 255, 0), "R")]:
        for r_idx, r in enumerate(rows):
            if debug_mode and (r_idx >= DEBUG_MAX_ROWS_DETAIL):
                continue

            decision = debug_row_decision(side, r, wall, center_x)
            if decision.get("row_is_title", False):
                continue

            if decision.get("fail_reason", "") == "no_num_near_wall":
                suppress_x = False
                if SUPPRESS_X_FOR_SUBJECT_ONLY_ROWS:
                    suppress_x = is_subject_only_row_like(side, r, wall, center_x)

                if debug_mode and DEBUG_DRAW_FAILED_MARKS and (not suppress_x):
                    xs = [e["x1"] for e in r["els"]] + [e["x2"] for e in r["els"]]
                    if xs:
                        sx1, sx2 = clip(min(xs), 0, w - 1), clip(max(xs), 0, w - 1)
                        y1, y2 = clip(r["y1"], 0, h - 1), clip(r["y2"], 0, h - 1)
                        mark_failed_row(final_img, sx1, y1, sx2, y2, (0, 0, 255))
                        put_text(final_img, sx1, y1 - 3, f"{side}{r_idx}:no_num", (0, 0, 255), 0.4, 1)
                continue

            if decision.get("fail_reason", "") in ["wall_is_nan", "subs_empty"]:
                if debug_mode and DEBUG_DRAW_FAILED_MARKS:
                    xs = [e["x1"] for e in r["els"]] + [e["x2"] for e in r["els"]]
                    if xs:
                        sx1, sx2 = clip(min(xs), 0, w - 1), clip(max(xs), 0, w - 1)
                        y1, y2 = clip(r["y1"], 0, h - 1), clip(r["y2"], 0, h - 1)
                        mark_failed_row(final_img, sx1, y1, sx2, y2, (0, 128, 255))
                        put_text(final_img, sx1, y1 - 3, f"{side}{r_idx}:fail", (0, 128, 255), 0.4, 1)
                continue

            subs = decision.get("subs", [])
            if not subs:
                continue

            # Visionのbboxでy範囲を軽く補正（近いcyのものだけ）
            v_match = [
                v for v in v_syms
                if abs(v["cy"] - r["cy"]) < 5
                and (v["cx"] < center_x if side == "L" else v["cx"] >= center_x)
            ]
            if v_match:
                y1 = min(v["y1"] for v in v_match)
                y2 = max(v["y2"] for v in v_match)
            else:
                y1, y2 = r["y1"], r["y2"]

            sx1 = min(p["x1"] for p in subs)
            sx2 = max(p["x2"] for p in subs)

            cv2.rectangle(final_img, (int(sx1 - 1), int(y1) - 1), (int(sx2 + 1), int(y2) + 1), color, 1)

            if debug_mode and DEBUG_DRAW_ROW_ID:
                put_text(final_img, int(sx1), int(y1) - 3, f"{side}{r_idx}", color, 0.5, 1)

    _ensure_parent_dir(output_file)
    ok = cv2.imwrite(output_file, final_img)
    if not ok:
        raise RuntimeError(f"failed to write output_file: {output_file}")

    return output_file
