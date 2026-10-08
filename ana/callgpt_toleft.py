import cv2
import numpy as np

# =========================================================
# 線抽出のための前処理（ぼかし差分法：ユーザー提示コードをそのまま使用）
# =========================================================
def extract_line_candidates(gray):
    # ぼかし（大きめ）で背景を均一化
    blur = cv2.GaussianBlur(gray, (31, 31), 0)

    # 差分 → 細い線だけ浮き上がる
    diff = cv2.absdiff(gray, blur)

    # 2値化（線が白になる）
    _, bw = cv2.threshold(diff, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    return diff, bw


# =========================================================
# 罫線を検出して削除（横線 → 縦線）※マスク生成ロジックはそのまま
# =========================================================
def del_lines_with_debug(gray):
    h, w = gray.shape

    # ---- 1. 線を浮き上がらせる ----
    diff, bw = extract_line_candidates(gray)

    # ---- 2. 横線マスク ----
    kernel_h = cv2.getStructuringElement(cv2.MORPH_RECT, (w // 20, 1))
    mask_h = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kernel_h)

    # ---- 3. 縦線マスク ----
    kernel_v = cv2.getStructuringElement(cv2.MORPH_RECT, (1, h // 20))
    mask_v = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kernel_v)

    # ---- 4. 横線 → 縦線の順で「グレースケール画像」から削除 ----
    no_h = cv2.bitwise_and(gray, gray, mask=cv2.bitwise_not(mask_h))
    no_hv = cv2.bitwise_and(no_h, no_h, mask=cv2.bitwise_not(mask_v))

    return diff, bw, mask_h, mask_v, no_h, no_hv


def callgpt_toleft(image_path,output_path):
    # =========================================================
    # 0) 入力画像パス
    # =========================================================
    # 必要に応じて差し替えてください

    # =========================================================
    # 1) 画像読み込み
    # =========================================================
    

    img = cv2.imread(image_path)
    if img is None:
        raise FileNotFoundError(image_path)

    # ------------------------------
    # 1. カラー → グレースケール＆反転表示
    # ------------------------------
    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img.copy()

    #print("【元画像（カラー）】")
    #plt.figure(figsize=(6, 6))
    #if len(img.shape) == 3:
    #    plt.imshow(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    #else:
    #    plt.imshow(img, cmap="gray")
    #plt.title("Original")
    #plt.axis("off")
    #plt.show()
    #plt.close()  # ★追加

    #print("【グレースケール画像】")
    #plt.figure(figsize=(6, 6))
    #plt.imshow(gray, cmap="gray")
    #plt.title("Gray")
    #plt.axis("off")
    #plt.show()
    #plt.close()  # ★追加

    # 1回目の白黒反転
    gray_inv1 = cv2.bitwise_not(gray)
    #print("【白黒反転（1回目）】")
    #plt.figure(figsize=(6, 6))
    #plt.imshow(gray_inv1, cmap="gray")
    #plt.title("Gray Inverted (1st)")
    #plt.axis("off")
    #plt.show()
    #plt.close()  # ★追加

    # 2回目の反転（元に戻る）
    gray_inv2 = cv2.bitwise_not(gray_inv1)
    #print("【さらに反転（2回目：元と同じ）】")
    #plt.figure(figsize=(6, 6))
    #plt.imshow(gray_inv2, cmap="gray")
    #plt.title("Gray Inverted (2nd)")
    #plt.axis("off")
    #plt.show()
    #plt.close()  # ★追加

    # ------------------------------
    # 2. グレースケール画像に対して罫線除去（ユーザー提示コード）
    # ------------------------------
    #print("【罫線除去前（グレースケール）】")
    #plt.figure(figsize=(6, 6))
    #plt.imshow(gray, cmap="gray")
    #plt.title("Before Line Removal (Gray)")
    #plt.axis("off")
    #plt.show()
    #plt.close()  # ★追加

    diff, bw, mask_h, mask_v, no_h, no_hv = del_lines_with_debug(gray)

    # ---- 途中経過表示（マスクが正しく生成されているか確認用） ----
    #print("【差分画像 diff（線が浮き上がる）】")
    #plt.figure(figsize=(6, 6))
    #plt.imshow(diff, cmap="gray")
    #plt.title("Line-enhanced (diff = abs(gray - blur))")
    #plt.axis("off")
    #plt.show()
    #plt.close()  # ★追加

    #print("【線候補2値画像 bw（線と文字が白）】")
    #plt.figure(figsize=(6, 6))
    #plt.imshow(bw, cmap="gray")
    #plt.title("Binary (lines white)")
    #plt.axis("off")
    #plt.show()
    #plt.close()  # ★追加
    #
    #print("【横線マスク】")
    #plt.figure(figsize=(6, 6))
    #plt.imshow(mask_h, cmap="gray")
    #plt.title("Horizontal Line Mask")
    #plt.axis("off")
    #plt.show()
    #plt.close()  # ★追加
    #
    #print("【縦線マスク】")
    #plt.figure(figsize=(6, 6))
    #plt.imshow(mask_v, cmap="gray")
    #plt.title("Vertical Line Mask")
    #plt.axis("off")
    #plt.show()
    #plt.close()  # ★追加
    #
    #print("【横線削除後（グレースケール）】")
    #plt.figure(figsize=(6, 6))
    #plt.imshow(no_h, cmap="gray")
    #plt.title("After Removing Horizontal Lines (Gray)")
    #plt.axis("off")
    #plt.show()
    #plt.close()  # ★追加

    #print("【縦線・横線削除後（グレースケール）】")
    #plt.figure(figsize=(6, 6))
    #plt.imshow(no_hv, cmap="gray")
    #plt.title("After Removing Horizontal + Vertical Lines (Gray)")
    #plt.axis("off")
    #plt.show()
    #plt.close()  # ★追加

    # ------------------------------
    # 3. 最終：白背景・黒文字・罫線なしの白黒2値画像を作成
    # ------------------------------
    # まず文書全体を2値化（網掛も含めて背景を白に寄せる）
    # Otsu により「黒い文字・罫線」と「それ以外（背景・薄い網掛）」を分離
    _, bin_doc = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # bin_doc: 文字・罫線 = 0(黒), 背景 = 255(白) になる想定

    #print("【文書2値（罫線含む：黒文字・黒線）】")
    #plt.figure(figsize=(6, 6))
    #plt.imshow(bin_doc, cmap="gray")
    #plt.title("Binary Document (Text + Lines)")
    #plt.axis("off")
    #plt.show()
    #plt.close()  # ★追加

    # マスクを使って罫線だけ白に塗りつぶす
    bin_no_h = bin_doc.copy()
    bin_no_h[mask_h > 0] = 255  # 横線マスク部分を白化

    bin_no_hv = bin_no_h.copy()
    bin_no_hv[mask_v > 0] = 255  # 縦線マスク部分を白化

    #print("【最終結果：白黒2値（罫線なし）】")
    #plt.figure(figsize=(6, 6))
    #plt.imshow(bin_no_hv, cmap="gray")
    #plt.title("Final Binary (No Lines)")
    #plt.axis("off")
    #plt.show()
    #plt.close()  # ★追加
    cv2.imwrite(image_path, bin_no_hv)
    
    
    ###########################################
    #↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑掛線削除↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑
    ###########################################
    img = cv2.imread(image_path)
    if img is None:
        raise FileNotFoundError(image_path)

    H, W = img.shape[:2]
    print("image size: width =", W, ", height =", H)

    # =========================================================
    # 2) 左側の「勘定科目欄」の自動推定
    # =========================================================
    gray_full = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur_full = cv2.GaussianBlur(gray_full, (5, 5), 0)
    _, bin_inv_full = cv2.threshold(
        blur_full, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )

    # 画像左側 45% を対象とする
    LEFT_RATIO = 0.45
    left_w = int(W * LEFT_RATIO)
    left_mask = bin_inv_full[:, :left_w]

    # --- 縦方向投影で y 範囲を決定 ---
    row_sum_full = left_mask.sum(axis=1) / 255.0
    row_max_full = row_sum_full.max()
    ROW_THRESH_RATIO = 0.12
    row_thresh_full = row_max_full * ROW_THRESH_RATIO
    rows_full = np.where(row_sum_full > row_thresh_full)[0]
    if rows_full.size == 0:
        raise RuntimeError("科目欄の縦範囲が検出できませんでした。閾値を調整してください。")
    y1 = int(rows_full[0])
    y2 = int(rows_full[-1])

    # --- 横方向投影で x 範囲を決定 ---
    col_sum_full = left_mask.sum(axis=0) / 255.0
    col_max_full = col_sum_full.max()
    COL_THRESH_RATIO = 0.30
    col_thresh_full = col_max_full * COL_THRESH_RATIO
    cols_full = np.where(col_sum_full > col_thresh_full)[0]
    if cols_full.size == 0:
        raise RuntimeError("科目欄の横範囲が検出できませんでした。閾値を調整してください。")
    x1 = int(cols_full[0])
    x2 = int(cols_full[-1])

    print("subject (勘定科目) area: x1 =", x1, ", y1 =", y1, ", x2 =", x2, ", y2 =", y2)

    subject_roi = img[y1:y2+1, x1:x2+1].copy()
    h_sub, w_sub = subject_roi.shape[:2]
    print("subject ROI size: width =", w_sub, ", height =", h_sub)

    # =========================================================
    # 3) 勘定科目欄を反転2値化
    # =========================================================
    gray_sub = cv2.cvtColor(subject_roi, cv2.COLOR_BGR2GRAY)
    blur_sub = cv2.GaussianBlur(gray_sub, (3, 3), 0)
    _, bin_inv_sub = cv2.threshold(
        blur_sub, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )

    # 細い画素の切れ目を埋めるクロージング
    kernel_close = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    bin_sub_close = cv2.morphologyEx(bin_inv_sub, cv2.MORPH_CLOSE, kernel_close, iterations=1)

    # 濁点など極小成分を安定して抽出するために、1ピクセルだけ膨張
    kernel_dil = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2, 2))
    bin_sub_proc = cv2.dilate(bin_sub_close, kernel_dil, iterations=1)

    print("subject ROI binary (inverted + closing + dilation):")

    # =========================================================
    # 4) 連結成分解析で「文字らしい成分」を抽出
    # =========================================================
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(bin_sub_proc)

    char_boxes = []
    char_centers_y = []
    char_heights = []

    # ノイズ除去用の下限値（濁点も残したいのでかなり緩く設定）
    MIN_AREA = 3
    MIN_W = 1
    MIN_H = 1

    for i in range(1, num_labels):  # 0 は背景
        x, y, w, h, area = stats[i]
        if area < MIN_AREA:
            continue
        if w < MIN_W or h < MIN_H:
            continue
        # 極端な縦線／横線は除外
        ratio = w / (h + 1e-6)
        if ratio < 0.10 or ratio > 8.0:
            continue

        cy = y + h / 2.0
        char_boxes.append((x, y, w, h))
        char_centers_y.append(cy)
        char_heights.append(h)

    print("initial detected char components:", len(char_boxes))
    if not char_boxes:
        raise RuntimeError("文字成分が検出できませんでした。閾値や前処理を調整してください。")

    char_centers_y = np.array(char_centers_y, dtype=np.float32)
    char_heights = np.array(char_heights, dtype=np.float32)
    median_h = float(np.median(char_heights))
    print("median character height:", median_h)

    # =========================================================
    # 5) 文字の中心 Y をソートし、1次元クラスタリングで行を特定
    # =========================================================
    sorted_indices = np.argsort(char_centers_y)
    char_boxes_sorted = [char_boxes[i] for i in sorted_indices]
    centers_y_sorted = char_centers_y[sorted_indices]

    if len(centers_y_sorted) >= 2:
        diffs = np.diff(centers_y_sorted)
        median_diff = float(np.median(diffs))
    else:
        median_diff = median_h
    print("median diff of centers:", median_diff)

    row_gap_thresh = max(median_h * 0.7, median_diff * 0.6)
    print("row gap threshold:", row_gap_thresh)

    rows_groups = []   # 各行 = [ (x,y,w,h), ... ]
    current_group = [char_boxes_sorted[0]]
    last_center = centers_y_sorted[0]

    for box, cy in zip(char_boxes_sorted[1:], centers_y_sorted[1:]):
        if cy - last_center > row_gap_thresh:
            rows_groups.append(current_group)
            current_group = [box]
        else:
            current_group.append(box)
        last_center = cy
    rows_groups.append(current_group)

    print("number of rows (from subject area only):", len(rows_groups))

    # =========================================================
    # 6) 行内での連結成分の結合（同一文字の部品をまとめる）
    #    ※ 行をまたいだ結合は行わない
    # =========================================================
    rows_groups_merged = []

    for row_idx, group in enumerate(rows_groups):
        n_comp = len(group)
        if n_comp == 0:
            rows_groups_merged.append([])
            continue

        # 行内コンポーネントの重心・高さ・面積
        cx = np.array([x + w / 2.0 for (x, y, w, h) in group], dtype=np.float32)
        cy = np.array([y + h / 2.0 for (x, y, w, h) in group], dtype=np.float32)
        h_arr = np.array([h for (x, y, w, h) in group], dtype=np.float32)
        area_arr = np.array([w * h for (x, y, w, h) in group], dtype=np.float32)

        median_h_row = float(np.median(h_arr))
        median_area_row = float(np.median(area_arr))

        if n_comp == 1:
            # 1 文字だけの行もそのまま保持（後で必ず転記）
            rows_groups_merged.append(group)
            continue

        # 行内での「最近傍距離」の分布からしきい値を自動決定
        nearest_dists = []
        for i in range(n_comp):
            best = float("inf")
            for j in range(n_comp):
                if i == j:
                    continue
                dx = float(cx[j] - cx[i])
                dy = float(cy[j] - cy[i])
                d = (dx * dx + dy * dy) ** 0.5
                if d < best:
                    best = d
            nearest_dists.append(best)

        nearest_dists_sorted = sorted(nearest_dists)
        idx_30 = max(0, int(len(nearest_dists_sorted) * 0.3) - 1)
        base_thresh = nearest_dists_sorted[idx_30]

        MERGE_DIST_BASE = base_thresh * 1.3
        MERGE_DIST_BASE = max(MERGE_DIST_BASE, 0.3 * median_h_row)
        MERGE_DIST_BASE = min(MERGE_DIST_BASE, 1.6 * median_h_row)

        MERGE_X_BASE = MERGE_DIST_BASE * 1.2
        MERGE_Y_BASE = MERGE_DIST_BASE * 1.2

        used = [False] * n_comp
        merged_row_boxes = []

        for i in range(n_comp):
            if used[i]:
                continue

            group_indices = [i]
            used[i] = True
            queue = [i]

            while queue:
                k = queue.pop()
                for j in range(n_comp):
                    if used[j]:
                        continue

                    dx = float(cx[j] - cx[k])
                    dy = float(cy[j] - cy[k])
                    dist = (dx * dx + dy * dy) ** 0.5

                    # 小さい面積のコンポーネントはより広い距離でマージを許可
                    if area_arr[k] < median_area_row * 0.25 or area_arr[j] < median_area_row * 0.25:
                        local_merge_dist = MERGE_DIST_BASE * 2.0
                        local_merge_x = MERGE_X_BASE * 2.0
                        local_merge_y = MERGE_Y_BASE * 2.0
                    else:
                        local_merge_dist = MERGE_DIST_BASE
                        local_merge_x = MERGE_X_BASE
                        local_merge_y = MERGE_Y_BASE

                    if (
                        dist <= local_merge_dist
                        and abs(dx) <= local_merge_x
                        and abs(dy) <= local_merge_y
                    ):
                        used[j] = True
                        group_indices.append(j)
                        queue.append(j)

            # グループに属する矩形を 1 つの外接矩形にまとめる
            xs = [group[idx][0] for idx in group_indices]
            ys = [group[idx][1] for idx in group_indices]
            xws = [group[idx][0] + group[idx][2] for idx in group_indices]
            yhs = [group[idx][1] + group[idx][3] for idx in group_indices]

            merged_x = int(min(xs))
            merged_y = int(min(ys))
            merged_w = int(max(xws) - merged_x)
            merged_h = int(max(yhs) - merged_y)

            merged_row_boxes.append((merged_x, merged_y, merged_w, merged_h))

        rows_groups_merged.append(merged_row_boxes)

    rows_groups = rows_groups_merged

    print("components after merging in each row:")
    for idx, g in enumerate(rows_groups):
        print("  row", idx, ":", len(g), "components")

    # =========================================================
    # 7) 行の y 範囲（青枠）を計算（最低高さを保証）
    # =========================================================
    subject_line_ranges = []
    MIN_LINE_HEIGHT = 6  # 最低行高さ（ピクセル）

    for group in rows_groups:
        ys = [b[1] for b in group]
        hs = [b[3] for b in group]

        raw_min = min(ys)
        raw_max = max(y + h for y, h in zip(ys, hs))

        row_y_min = max(0, raw_min - 2)
        row_y_max = min(h_sub - 1, raw_max + 2)

        # 最低行高さを保証
        if row_y_max - row_y_min < MIN_LINE_HEIGHT:
            row_y_max = min(h_sub - 1, row_y_min + MIN_LINE_HEIGHT)

        subject_line_ranges.append((row_y_min, row_y_max))

    print("subject line ranges:", subject_line_ranges)

    # 行内マージ後の全文字ボックス一覧を作成
    char_boxes_merged = []
    for group in rows_groups:
        for box in group:
            char_boxes_merged.append(box)

    # =========================================================
    # 8) 結果描画：行（青枠）＋文字（緑枠）※確認用
    # =========================================================
    vis = img.copy()

    # 行：青枠
    for (rs, re) in subject_line_ranges:
        top = y1 + rs
        bottom = y1 + re
        cv2.rectangle(
            vis,
            (x1, top),
            (x2, bottom),
            (255, 0, 0),  # 青
            1
        )

    # 文字：緑枠
    for (x, y, w, h) in char_boxes_merged:
        cv2.rectangle(
            vis,
            (x1 + x, y1 + y),
            (x1 + x + w, y1 + y + h),
            (0, 255, 0),  # 緑
            1
        )

    print("final result: blue = rows (from subject inverted image), green = merged characters")

    # =========================================================
    # 9) 科目欄をクリアした画像 temp を作成
    # =========================================================
    temp = img.copy()
    cv2.rectangle(
        temp,
        (x1, y1),
        (x2, y2),
        (255, 255, 255),  # 白
        thickness=-1      # 塗りつぶし
    )

    # =========================================================
    # 10) 各行の文字を左寄せして temp にペースト
    # =========================================================
    CHAR_GAP = 2  # 文字間の最小スペース（ピクセル）

    for row_idx, group in enumerate(rows_groups):
        if not group:
            continue

        # 行内の文字を x 座標で昇順ソート（左から右）
        group_sorted = sorted(group, key=lambda b: b[0])

        # 科目欄 ROI 内での左寄せ開始位置（少し余白を持たせる）
        cur_x_sub = 2

        for (x, y, w, h) in group_sorted:
            # 元画像から文字パッチを取得（カラー）
            src_x1 = x1 + x
            src_y1 = y1 + y
            src_x2 = src_x1 + w
            src_y2 = src_y1 + h
            char_patch = img[src_y1:src_y2, src_x1:src_x2].copy()

            # ROI 左端からの配置位置を計算
            # はみ出す場合は左端ギリギリに詰めて貼る
            if cur_x_sub + w > w_sub:
                cur_x_sub = max(0, w_sub - w)

            dst_x1 = x1 + cur_x_sub
            dst_y1 = y1 + y
            dst_x2 = dst_x1 + w
            dst_y2 = dst_y1 + h

            # 念のため、右端側が ROI を超える場合は切り詰めて貼る
            if dst_x2 > x1 + w_sub:
                overlap_w = (x1 + w_sub) - dst_x1
                if overlap_w <= 0:
                    # どうしても全く収まらない場合はスキップ
                    continue
                char_patch = char_patch[:, :overlap_w]
                dst_x2 = x1 + w_sub

            # temp にペースト
            temp[dst_y1:dst_y2, dst_x1:dst_x2] = char_patch

            # 次の文字位置を更新（前の文字の右端＋一定間隔）
            cur_x_sub += w + CHAR_GAP

    # =========================================================
    # 11) 出来上がった画像を保存＆表示
    # =========================================================
    cv2.imwrite(output_path, temp)
    print("saved:", output_path)