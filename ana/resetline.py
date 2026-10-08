# １．罫線を消す
import cv2
import numpy as np
#from matplotlib import pyplot as plt

# ==========================
#  縦線・横線除去関数
# ==========================
def del_lines(img):
    img_disp = img.copy()
    kernel = np.zeros((5, 5), np.uint8)
    kernel[2, :] = 1               # 横線検出用（水平）カーネル
    kernel2 = np.zeros((5, 5), np.uint8)
    kernel2[:, 2] = 1              # 縦線検出用（垂直）カーネル
    kernel3 = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))

    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img.copy()

    gray2 = gray.copy()
    h, w = gray.shape

    # ==========================
    # 横線除去処理（クロージング→反転→二値化→短い横線の除去）
    # ==========================
    img_th = cv2.dilate(gray, kernel, iterations=10)
    img_th = cv2.erode(img_th, kernel, iterations=10)
    img_th = 255 - img_th
    img_th = np.where(img_th > 20, 255, 0).astype(np.uint8)

    # ---- 短い横線（短い連結成分）を黒で塗り潰して除去 ----
    min_len_h = max(400, w // 40)  # 横線の最小長さしきい値（必要に応じて調整）
    num_labels_h, labels_h, stats_h, _ = cv2.connectedComponentsWithStats(img_th, connectivity=8)
    img_th_filtered = np.zeros_like(img_th)

    for i in range(1, num_labels_h):  # 0は背景
        x, y, ww, hh, area = stats_h[i]
        # 横長かつしきい値以上の十分に長い横線のみ残す
        if ww >= hh * 2 and ww >= min_len_h:
            img_th_filtered[labels_h == i] = 255

    img_th = img_th_filtered

    img_th2_h = cv2.dilate(img_th, kernel2, iterations=1)
    img_th3_h = cv2.dilate(img_th2_h, kernel3)
    img_th4 = cv2.morphologyEx(img_th3_h, cv2.MORPH_OPEN, kernel3)
    img_th4 = np.where(img_th4 > 5, 255, 0).astype(np.uint8)
    # 横線を除去した中間画像（文字は gray を保持、線は白で塗り潰し）
    img_th4 = np.where(img_th4 <= 50, gray, 255).astype(np.uint8)

    # ==========================
    # 縦線除去処理（横線と同じやり方）
    # ==========================
    img_v = cv2.dilate(gray2, kernel2, iterations=15)
    img_v = cv2.erode(img_v, kernel2, iterations=15)
    img_v = 255 - img_v
    img_v = np.where(img_v > 20, 255, 0).astype(np.uint8)

    # ---- 短い縦線（短い連結成分）を黒で塗り潰して除去 ----
    min_len_v = max(200, h // 40)  # 縦線の最小長さしきい値（必要に応じて調整）
    num_labels_v, labels_v, stats_v, _ = cv2.connectedComponentsWithStats(img_v, connectivity=8)
    img_v_filtered = np.zeros_like(img_v)

    for i in range(1, num_labels_v):  # 0は背景
        x, y, ww, hh, area = stats_v[i]
        # 縦長かつしきい値以上の十分に長い縦線のみ残す
        if hh >= ww * 2 and hh >= min_len_v:
            img_v_filtered[labels_v == i] = 255

    img_th3 = img_v_filtered  # 以降の変数名互換（白＝除去対象の縦線）

    # ---- 平滑化 ----
    img_th3 = cv2.morphologyEx(img_th3, cv2.MORPH_OPEN, kernel3)

    # ---- 縦線マスクで白塗りして最終合成 ----
    img_disp3 = np.where(img_th3 > 0, 255, img_th4).astype(np.uint8)

    # 線分検出（確認用）
    lines = cv2.HoughLinesP(
        img_th3, rho=1, theta=np.pi/360,
        threshold=100, minLineLength=max(200, min_len_v),
        maxLineGap=6
    )

    img_disp3 = np.array(img_disp3, dtype=np.uint8)
    img_th3 = np.where(img_th3 > 5, 255, 0).astype(np.uint8)

    return img_disp3, stats_v.tolist(), img_th3, lines




def estimate_content_bbox(gray: np.ndarray):
    """
    紙の余白を除いた内容領域の外接矩形 (x, y, w, h) を返す。
    """
    h, w = gray.shape
    not_white = (gray < 245).astype(np.uint8) * 255
    not_white = cv2.medianBlur(not_white, 5)

    ys, xs = np.where(not_white > 0)
    if len(ys) == 0:
        # 何も検出できない場合は画像全体
        return (0, 0, w, h)

    y_min, y_max = int(np.percentile(ys, 1)), int(np.percentile(ys, 99))
    x_min, x_max = int(np.percentile(xs, 1)), int(np.percentile(xs, 99))

    pad = max(5, min(h, w) // 200)
    x0 = max(0, x_min - pad)
    y0 = max(0, y_min - pad)
    x1 = min(w - 1, x_max + pad)
    y1 = min(h - 1, y_max + pad)

    return (x0, y0, x1 - x0 + 1, y1 - y0 + 1)


def estimate_pitch_from_components(gray: np.ndarray, bbox):
    """
    文字コンポーネントの高さ中央値からピッチを推定。
    返り値: (pitch or None, debug_info: dict)
    """
    x, y, bw, bh = bbox
    roi = gray[y:y + bh, x:x + bw]

    bw_img = cv2.adaptiveThreshold(
        roi, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        35, 15
    )
    bw_img = cv2.medianBlur(bw_img, 3)

    numLabels, labels, stats, cents = cv2.connectedComponentsWithStats(
        bw_img, connectivity=8
    )
    if numLabels <= 1:
        return None, {}

    areas = stats[1:, cv2.CC_STAT_AREA]
    heights = stats[1:, cv2.CC_STAT_HEIGHT]
    widths = stats[1:, cv2.CC_STAT_WIDTH]

    mask = (areas > 10) & (heights >= 6) & (heights <= 60) & (widths <= 120)
    heights_sel = heights[mask]

    if len(heights_sel) < 30:
        return None, {
            "num_cc": int(numLabels - 1),
            "num_sel": int(len(heights_sel)),
        }

    med_h = float(np.median(heights_sel))
    # 文字高さ × 1.85 を基準とした罫線ピッチ
    pitch = int(round(np.clip(med_h * 1.85, 10, 60)))
    return pitch, {
        "num_cc": int(numLabels - 1),
        "num_sel": int(len(heights_sel)),
        "med_h": med_h,
        "pitch": pitch,
    }


def estimate_pitch(gray: np.ndarray, bbox):
    """
    ピッチ推定（優先: 文字コンポーネント → 既定行数のフォールバック）
    """
    pitch_cc, info = estimate_pitch_from_components(gray, bbox)
    if pitch_cc is not None:
        return pitch_cc, "cc", info

    # フォールバック：内容領域を約33本の罫線で分割
    x, y, bw, bh = bbox
    pitch_fb = max(10, int(round(bh / 33)))
    pitch_fb = int(np.clip(pitch_fb, 12, 48))

    return pitch_fb, "fallback", info


def detect_existing_hlines(gray: np.ndarray):
    """
    もともとの帳票にある長い水平線の y 座標を検出する関数。
    今回は「ノート罫をとぎれさせない」ことを優先するため、
    結果は参照のみで、スキップには使わない。
    """
    h, w = gray.shape
    inv = cv2.bitwise_not(gray)

    k = max(25, w // 30)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k, 1))
    lines = cv2.morphologyEx(inv, cv2.MORPH_OPEN, kernel, iterations=1)

    row_sum = lines.sum(axis=1)
    if np.any(row_sum > 0):
        thr = np.percentile(row_sum[row_sum > 0], 60)
        ys = np.where(row_sum >= thr)[0]
    else:
        ys = np.array([], dtype=int)

    return set(int(v) for v in ys)


def draw_notebook_lines(img: np.ndarray,
                        bbox,
                        pitch: int,
                        color=(190, 190, 230),
                        alpha=0.50,
                        thickness=1,
                        start_offset_ratio=0.55):
    """
    内容領域内に一定ピッチでノート罫を描画する。
    ・既存の水平罫線を「避けない」
    ・y_start 〜 y_end の範囲を pitch 間隔で必ず埋める
    ので、行間に罫線の抜けが生じにくい。
    """
    x, y, bw, bh = bbox
    h, w = img.shape[:2]

    overlay = img.copy()
    ys = []

    # 最初の罫線の位置
    y_start = y + int(round(pitch * start_offset_ratio))
    y_end = y + bh - 1

    for yy in range(y_start, y_end, pitch):
        ys.append(yy)
        cv2.line(
            overlay,
            (0, yy),
            (w - 1, yy),
            color,
            thickness,
            cv2.LINE_AA
        )

    out = cv2.addWeighted(overlay, alpha, img, 1 - alpha, 0)
    return out, ys


def resetline_miz(input_path):
    # ==========================
    #  画像読み込みと処理
    # ==========================
    output_path = input_path+".syori.png"
    SRC_PATH = output_path               # 入力画像
    DST_PATH = input_path+".ok.png"         # 出力画像（同サイズ）
    DBG_OVERLAY = input_path+"debug.png"

    # ==========================
    #  画像読み込みと処理
    # ==========================

    img = cv2.imread(input_path)
    if img is None:
        raise FileNotFoundError(f"指定された画像が見つかりません: {input_path}")

    processed_img, stats, img_th3, lines = del_lines(img)

    cv2.imwrite(output_path, processed_img)
    print(f"線除去後の画像を保存しました: {output_path}")

    #plt.figure(figsize=(10, 10))
    #plt.imshow(cv2.cvtColor(processed_img, cv2.COLOR_BGR2RGB))
    #plt.title("Lines Removed")
    #plt.axis("off")
    #plt.show()

    LINE_THICKNESS = 1                            # 罫線の太さ(px)
    LINE_COLOR = (190, 190, 230)                  # BGR：淡いブルーグレー
    LINE_ALPHA = 0.50                             # 罫線の透明度（0.0〜1.0）
    START_OFFSET_RATIO = 0.55                     # 先頭線の開始オフセット（ピッチ比）
    # ノート罫を描画する元画像（罫線除去後）
    img = cv2.imread(SRC_PATH)
    if img is None:
        raise FileNotFoundError(SRC_PATH)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # 内容領域とピッチの推定
    bbox = estimate_content_bbox(gray)
    pitch, method, info = estimate_pitch(gray, bbox)

    # 既存水平線の検出（今回はデバッグ用途のみ）
    existing_rows = detect_existing_hlines(gray)
    print("pitch:", pitch, "method:", method)
    print("existing horizontal lines (sample):",
          sorted(list(existing_rows))[:10])

    # ノート罫を描画（既存水平罫は避けない）
    out, ys = draw_notebook_lines(
        img,
        bbox,
        pitch,
        color=LINE_COLOR,
        alpha=LINE_ALPHA,
        thickness=LINE_THICKNESS,
        start_offset_ratio=START_OFFSET_RATIO,
    )

    # 結果画像の保存
    cv2.imwrite(DST_PATH, out)

    # デバッグ用に内容領域と線位置を可視化
    dbg = img.copy()
    x, y, bw, bh = bbox
    cv2.rectangle(dbg, (x, y), (x + bw - 1, y + bh - 1),
                  (0, 0, 255), 1)
    for yy in ys:
        cv2.circle(dbg, (10, yy), 2, (0, 0, 255), -1)
    cv2.imwrite(DBG_OVERLAY, dbg)