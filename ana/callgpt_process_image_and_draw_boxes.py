# -*- coding: utf-8 -*-
"""勘定科目のエリア抽出 - フォルダ内全画像処理（正規表現ヘッダー判定 & 行ごと右端確定版） - 最終修正版 v2.8
外部モジュールとして import して使えるように実行部分をメソッド化
Colab 用の表示機能は削除済み
"""

import cv2
import numpy as np
import re  # 正規表現モジュール
import glob  # フォルダ内のファイルを取得
from google.cloud import vision
from google.cloud.vision_v1 import types
import os

# ★ Vision API サービスアカウントキーのパス（固定）
VISION_API_ACCOUNT_FILE = "/srv/www/apps/analygent_backend/internal/service-account-file.json"


# --- OCR 座標処理クラス (Bound) ---
class Bound:
    def __init__(self, vertices):
        xs = [v.x for v in vertices]
        ys = [v.y for v in vertices]
        self._left = min(xs)
        self._right = max(xs)
        self._top = min(ys)
        self._bottom = max(ys)

    def get_left(self):
        return self._left

    def get_right(self):
        return self._right

    def get_top(self):
        return self._top

    def get_bottom(self):
        return self._bottom


# --- 判定関数 ---
def is_numerical_text(text):
    if not text.strip():
        return False
    # 数値、全角数値、金額記号
    if any(c.isdigit() or c in "０１２３４５６７８９" for c in text):
        return True
    if text in [",", ".", "(", ")", "△", "-", "+", "¥", "円", " "]:
        return True
    return False


# TKCや短い記号を除外するリスト
EXCLUDE_TOKENS = ["TKC", "tkc", "T", "K", "C", "一", "-", "…", "・", "|", "/", ":", "(", ")"]


def is_account_text(text):
    if not text.strip():
        return False

    # 数値、または金額を構成する記号は除外
    if is_numerical_text(text):
        return False

    # TKCのような特定の短い文字列（トークン全体）のみを除外
    if text.upper() in EXCLUDE_TOKENS:
        return False

    return True


# ヘッダー判定用 正規表現パターン
DATE_HEADER_PATTERN = re.compile(
    r"(平成|令和|昭和|大正|明治)?\d{1,4}[元]?年(\d{1,2}月)?(\d{1,2}日)?"
    r"|\d{4}[/-]\d{1,2}[/-]\d{1,2}"
    r"|(計算書|報告書|決算書|連結|貸借対照表|損益計算書|自|至|期間|科目|金額|費目|円|千円)"
)


# ヘッダー行判定関数
def is_header_row(row_text):
    """行のテキスト全体がヘッダーパターンに一致するか判定する"""
    return DATE_HEADER_PATTERN.search(row_text) is not None


# --- Vision API OCR ---
def visionAPI(fname):
    # サービスアカウントファイルのパスが存在しない場合は処理をスキップ
    # [pmj-ana] Cloud Run には鍵ファイルが無く ADC を使うので、このチェックはしない
    if False and not os.path.exists(VISION_API_ACCOUNT_FILE):
        print(
            f"警告: サービスアカウントファイルが見つかりません。Vision APIの処理をスキップします。\n"
            f"  パス: {VISION_API_ACCOUNT_FILE}"
        )
        return []

    # [pmj-ana] 鍵ファイルが無い環境(Cloud Run)では ADC を使う
    if os.path.exists(VISION_API_ACCOUNT_FILE):
        client = vision.ImageAnnotatorClient.from_service_account_json(VISION_API_ACCOUNT_FILE)
    else:
        client = vision.ImageAnnotatorClient()

    img = cv2.imread(fname)
    if img is None:
        print(f"エラー: 画像ファイルが見つかりません - {fname}")
        return []

    _, content = cv2.imencode(".jpg", img)
    image = types.Image(content=content.tobytes())

    response = client.document_text_detection(
        image=image, image_context={"language_hints": ["ja-t-i0-handwrit"]}
    )

    characters = []
    document = response.full_text_annotation

    for page in document.pages:
        for block in page.blocks:
            for paragraph in block.paragraphs:
                for word in paragraph.words:
                    for symbol in word.symbols:
                        characters.append(
                            {
                                "text": symbol.text,
                                "left": Bound(symbol.bounding_box.vertices).get_left(),
                                "top": Bound(symbol.bounding_box.vertices).get_top(),
                                "right": Bound(symbol.bounding_box.vertices).get_right(),
                                "bottom": Bound(symbol.bounding_box.vertices).get_bottom(),
                            }
                        )
    return characters


# --- エリアと行座標を特定 (再修正ロジック v2.5) ---
def identify_account_area_and_lines_final(characters, original_img_path):
    img = cv2.imread(original_img_path)
    if img is None:
        return None, []

    width, height = img.shape[1], img.shape[0]

    # 1. 全文字から行をまとめ、ヘッダー判定に利用
    all_chars_sorted = sorted(characters, key=lambda x: x["top"])
    temp_all_rows = []

    if all_chars_sorted:
        current_row_chars = []
        for char in all_chars_sorted:
            if not current_row_chars:
                current_row_chars.append(char)
            else:
                # 行まとめのY軸閾値 7
                if char["top"] - current_row_chars[-1]["top"] < 7:
                    current_row_chars.append(char)
                else:
                    row_text = "".join(c["text"] for c in current_row_chars)
                    y_min = min(c["top"] for c in current_row_chars)
                    y_max = max(c["bottom"] for c in current_row_chars)
                    temp_all_rows.append(
                        {
                            "text": row_text,
                            "y_min": y_min,
                            "y_max": y_max,
                            "chars": current_row_chars,
                        }
                    )
                    current_row_chars = [char]

        if current_row_chars:
            row_text = "".join(c["text"] for c in current_row_chars)
            y_min = min(c["top"] for c in current_row_chars)
            y_max = max(c["bottom"] for c in current_row_chars)
            temp_all_rows.append(
                {
                    "text": row_text,
                    "y_min": y_min,
                    "y_max": y_max,
                    "chars": current_row_chars,
                }
            )

    # 2. ヘッダーではない行にある数値のみを抽出し、金額エリア決定の基準とする
    non_header_num_bounds = []
    for temp_row in temp_all_rows:
        if is_header_row(temp_row["text"]):
            continue

        for char in temp_row["chars"]:
            if is_numerical_text(char["text"]):
                non_header_num_bounds.append(
                    (char["left"], char["top"], char["right"], char["bottom"])
                )

    # 3. 勘定科目エリアの最大右端 (max_account_x_end) を決定
    num_x_min_list_for_x_end = [b[0] for b in non_header_num_bounds if b[0] > width // 2]

    max_account_x_end = width * 0.6  # デフォルト
    if num_x_min_list_for_x_end:
        min_num_x = min(num_x_min_list_for_x_end)
        max_account_x_end = min_num_x - 2
        max_account_x_end = max(width * 0.4, max_account_x_end)

    # 4. 勘定科目候補 (account_chars) の抽出
    account_chars = []
    for c in characters:
        if is_account_text(c["text"]) and c["right"] < max_account_x_end + 15:
            account_chars.append(c)

    if not account_chars:
        return None, []

    # 5. 勘定科目候補で構成される行データ (rows_data)
    account_chars_sorted = sorted(account_chars, key=lambda x: x["top"])
    rows_data = []

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
                        rows_data.append(
                            {"y_start": y_min, "y_end": y_max, "chars": current_row_chars}
                        )
                    current_row_chars = [char]

        if current_row_chars:
            y_min = min(c["top"] for c in current_row_chars)
            y_max = max(c["bottom"] for c in current_row_chars)
            row_text = "".join(c["text"] for c in current_row_chars)
            if not is_header_row(row_text):
                rows_data.append(
                    {"y_start": y_min, "y_end": y_max, "chars": current_row_chars}
                )

    # 6. 数値がある行だけを採用し、行ごとの左端と右端を決定
    filtered_rows = []
    last_y_end = -1

    # 金額エリアの目安
    num_x_min_list_for_amount = [b[0] for b in non_header_num_bounds if b[0] > width // 3]
    num_area_start_x = width // 2
    if num_x_min_list_for_amount:
        num_area_start_x = min(num_x_min_list_for_amount) - 10

    Y_PADDING = 2  # Y軸パディング

    for row in rows_data:
        y1_raw, y2_raw = row["y_start"], row["y_end"]
        row_chars = row["chars"]
        has_amount = False

        # 金額エリアに数値が存在するか確認
        for bx1, by1, bx2, by2 in non_header_num_bounds:
            if bx1 >= num_area_start_x + 5:
                if min(by2, y2_raw + Y_PADDING) - max(by1, y1_raw - Y_PADDING) > 0:
                    has_amount = True
                    break

        if has_amount:
            row_x_start_raw = min(c["left"] for c in row_chars)
            X_START_PADDING = 20
            x_start_row = max(0, row_x_start_raw - X_START_PADDING)

            row_x_end_raw = max(c["right"] for c in row_chars)
            X_END_PADDING = 5
            x_end_row = min(row_x_end_raw + X_END_PADDING, int(max_account_x_end) + 10)

            y_start = max(y1_raw - Y_PADDING, last_y_end + 1)
            y_end = y2_raw + Y_PADDING

            if y_start < y_end:
                filtered_rows.append((x_start_row, y_start, y_end, x_end_row))
                last_y_end = y_end

    return None, filtered_rows


# --- 単一画像を処理し、緑枠付き画像を保存する関数 ---
def process_image_and_draw_boxes(input_image_path, output_image_path=None):
    """
    単一画像に対して勘定科目エリアを検出し、緑枠を描画した画像を保存する。

    Parameters
    ----------
    input_image_path : str
        入力画像のパス
    output_image_path : str or None
        緑枠を描画した画像の出力パス（None の場合は保存しない）

    Returns
    -------
    bool
        正常に処理・（必要なら）保存できた場合 True, それ以外 False
    """
    print(f"\n--- 処理中: {input_image_path} ---")

    # [pmj-ana] Cloud Run には鍵ファイルが無く ADC を使うので、このチェックはしない
    if False and not os.path.exists(VISION_API_ACCOUNT_FILE):
        print(
            f"エラー: Vision APIアカウントファイルが見つからないため、処理を中断します。\n"
            f"  パス: {VISION_API_ACCOUNT_FILE}"
        )
        return False

    characters = visionAPI(input_image_path)
    if not characters:
        print("OCR結果が取得できませんでした。")
        return False

    img = cv2.imread(input_image_path)
    if img is None:
        print("画像を読み込めません。")
        return False

    area, rows_with_x_end = identify_account_area_and_lines_final(characters, input_image_path)
    if not rows_with_x_end:
        print("抽出エリアを特定できませんでした。")
        return False

    COLOR_GREEN = (0, 255, 0)
    img_out = img.copy()

    for x_start, y1, y2, x_end in rows_with_x_end:
        x_start = max(0, x_start)
        y1 = max(0, y1)
        x_end = min(img_out.shape[1] - 1, x_end)
        y2 = min(img_out.shape[0] - 1, y2)

        if x_start < x_end and y1 < y2:
            cv2.rectangle(img_out, (x_start, y1), (x_end, y2), COLOR_GREEN, 2)

    if output_image_path is not None:
        os.makedirs(os.path.dirname(output_image_path), exist_ok=True)
        cv2.imwrite(output_image_path, img_out)
        print(f"出力画像を保存しました: {output_image_path}")

    return True


# --- フォルダ内の全画像を処理するメソッド ---
def process_folder(input_dir, output_dir):
    """
    指定フォルダ内の全画像に対して勘定科目エリア抽出 + 緑枠描画を行う。

    Parameters
    ----------
    input_dir : str
        入力画像フォルダのパス（末尾スラッシュ不要でもOK）
    output_dir : str
        出力画像フォルダのパス（存在しなければ自動作成）

    Returns
    -------
    dict
        {
            "total": 処理対象画像数,
            "succeeded": 正常処理できた枚数,
            "failed": [失敗したファイルパスのリスト]
        }
    """
    print(f"対象フォルダ: {input_dir}")

    if not input_dir.endswith("/"):
        input_dir = input_dir + "/"

    image_files = []
    image_files.extend(glob.glob(input_dir + "*.jpg"))
    image_files.extend(glob.glob(input_dir + "*.jpeg"))
    image_files.extend(glob.glob(input_dir + "*.png"))
    image_files.extend(glob.glob(input_dir + "*.JPG"))
    image_files.extend(glob.glob(input_dir + "*.PNG"))

    print(f"検出された画像ファイル数: {len(image_files)}")

    if not image_files:
        print("指定されたフォルダに画像ファイルが見つかりませんでした。")
        return {"total": 0, "succeeded": 0, "failed": []}

    os.makedirs(output_dir, exist_ok=True)

    succeeded = 0
    failed = []

    for img_path in image_files:
        base_name = os.path.basename(img_path)
        output_path = os.path.join(output_dir, base_name)

        ok = process_image_and_draw_boxes(img_path, output_image_path=output_path)
        if ok:
            succeeded += 1
        else:
            failed.append(img_path)

    print("\n--- 全ての画像の処理が完了しました ---")
    print(f"成功: {succeeded} / {len(image_files)}")
    if failed:
        print("失敗したファイル:")
        for f in failed:
            print("  -", f)

    return {"total": len(image_files), "succeeded": succeeded, "failed": failed}


# このファイルを直接 python 実行したとき用（任意）
if __name__ == "__main__":
    input_dir = "/path/to/input_dir"
    output_dir = "/path/to/output_dir"
    process_folder(input_dir, output_dir)
