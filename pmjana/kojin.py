# -*- coding: utf-8 -*-
"""個人(所得税青色申告決算書・一般用): 読み取り → v2ac の個人用 96 行(engine2.Analyze_kojin と同じ形)

  96 行 = 損益計算書 45 行(様式の欄番号 ①〜㊺) + 貸借対照表 50 行 + 雑収入 1 行。
  zaiTask の編集画面と make.do は「この順番の 96 行」を前提にしている(行の番号で検算・編集可否を決める)。

  固定表 CL1 / CL2 と、空欄行(自由記入欄)の照合の仕方は
  pmj-aitext-1 / engine/engine2.py(= test1 /var/www/html/pys/engine/engine2.py)の
  Analyze_kojin(6245-6684 行)の写し。
  読み取りは OCR の座標ではなく、OpenAI に様式の欄番号つきで読ませる(prompts/kojin_aoiro.txt)。

  決まりごと(2026-10-08 ユーザー回答):
    ・空欄の経費が6枠(㉕〜㉚)を超えたら、超えた分は雑費(㉛)に足す
    ・貸借対照表の期首(1月1日)は入れない(前期欄は空。元のエンジンと同じ)
    ・不動産所得用 / 農業所得用 / 白色の収支内訳書は対象外(エラーにする)
"""
import base64
import json
import os
import re
import unicodedata

from pmjana import master
from pmjana.houjin import to_amount

PROMPT_PATH = os.path.join(os.path.dirname(__file__), "prompts", "kojin_aoiro.txt")
SUPPORTED_FORM = "青色申告決算書(一般用)"

# ---- engine2.py 6245-6290 (損益計算書 45 行): [名前, order, family, genus, species, variety, property] ----
CL1 = [
    ["売上（収入）金額", 1, 1, 0, 0, 0, 1], ["期首棚卸高", 1, 2, 1, 0, 4, -1], ["仕入金額", 1, 2, 2, 0, 0, -1],
    ["小計", 1, 2, 0, 0, -3, 1], ["期末棚卸高", 1, 2, 3, 0, 6, 1], ["差引原価", 1, 2, 0, 0, -9, 1],
    ["差引金額", 1, 3, 0, 0, -4, 1], ["租税公課", 1, 4, 2, 2, 1, -1], ["荷造運賃", 1, 4, 2, 13, 4, -1],
    ["水道光熱費", 1, 4, 2, 3, 1, -1], ["旅費交通費", 1, 4, 2, 4, 3, -1], ["通信費", 1, 4, 2, 5, 1, -1],
    ["広告宣伝費", 1, 4, 2, 15, 1, -1], ["接待交際費", 1, 4, 2, 6, 1, -1], ["損害保険料", 1, 4, 2, 7, 3, -1],
    ["修繕費", 1, 4, 2, 8, 1, -1], ["消耗品費", 1, 4, 2, 9, 1, -1], ["減価償却費", 1, 4, 2, 1, 1, -1],
    ["福利厚生費", 1, 4, 1, 8, 1, -1], ["給料賃金", 1, 4, 1, 0, 1, -1], ["外注工賃", 1, 4, 2, 14, 3, -1],
    ["利子割引料", 1, 7, 0, 2, 9, -1], ["地代家賃", 1, 4, 2, 11, 1, -1], ["貸倒金", 1, 4, 2, 38, 2, -1],
    ["", 2, 999, 0, 0, 0, -1], ["", 2, 999, 0, 0, 0, -1], ["", 2, 999, 0, 0, 0, -1],
    ["", 2, 999, 0, 0, 0, -1], ["", 2, 999, 0, 0, 0, -1], ["", 2, 999, 0, 0, 0, -1],
    ["雑費", 1, 4, 2, 45, 1, -1], ["経費の計", 1, 20, 0, 0, 1, -1], ["経費の差引金額", 1, 5, 0, 0, -6, -1],
    ["貸倒引当金戻入", 1, 6, 0, 3, 1, -1], ["", 2, 999, 0, 0, 0, -1], ["", 2, 999, 0, 0, 0, -1],
    ["計", 1, 6, 0, 0, -4, -1], ["専従者給与", 1, 4, 1, 0, 2, -1], ["貸倒引当金繰入", 1, 4, 2, 36, 1, -1],
    ["", 2, 999, 0, 0, 0, -1], ["", 2, 999, 0, 0, 0, -1], ["計", 1, 7, 0, 0, -4, -1],
    ["青色申告特別控除前の所得金額", 1, 11, 0, 0, 1, -1], ["青色申告特別控除額", 1, 20, 0, 0, 2, -1],
    ["所得金額", 1, 11, 0, 0, -7, -1],
]
# ---- engine2.py 6442-6492 (貸借対照表 50 行) ----
CL2 = [
    ["現金", 2, 10, 1, 1, 6, 1], ["当座預金", 2, 10, 1, 1, 13, 1], ["定期預金", 2, 10, 1, 1, 15, 1],
    ["その他の預金", 2, 10, 1, 1, 42, 1], ["受取手形", 2, 10, 2, 3, 1, 1], ["売掛金", 2, 10, 2, 0, 7, 1],
    ["有価証券", 2, 10, 1, 3, 1, 1], ["棚卸資産", 2, 10, 3, 1, 2, 1], ["前払い金", 2, 10, 4, 0, 5, 1],
    ["貸付金", 2, 10, 4, 9, 2, 1], ["建物", 2, 20, 1, 6, 2, 1], ["建物付属設備", 2, 20, 1, 6, 3, 1],
    ["機械装置", 2, 20, 1, 4, 2, 1], ["車輌運搬具", 2, 20, 1, 9, 2, 1], ["工具器具備品", 2, 20, 1, 8, 2, 1],
    ["土地", 2, 20, 1, 12, 1, 1],
    [None, 2, 999, 0, 0, 0, -1], [None, 2, 999, 0, 0, 0, -1], [None, 2, 999, 0, 0, 0, -1], [None, 2, 999, 0, 0, 0, -1],
    [None, 2, 999, 0, 0, 0, -1], [None, 2, 999, 0, 0, 0, -1], [None, 2, 999, 0, 0, 0, -1],
    ["事業主貸 ", 2, 10, 4, 0, 7, 1], ["資産の部合計", 2, 35, 0, 0, 0, 1],
    ["支払手形", 2, 40, 1, 1, 1, 1], ["買掛金", 2, 40, 1, 0, 1, 1], ["借入金", 2, 40, 2, 0, 2, 1],
    ["未払金", 2, 40, 3, 15, 1, 1], ["前受金", 2, 40, 3, 8, 1, 1], ["預り金", 2, 40, 3, 20, 1, 1],
    [None, 2, 999, 0, 0, 0, -1], [None, 2, 999, 0, 0, 0, -1], [None, 2, 999, 0, 0, 0, -1], [None, 2, 999, 0, 0, 0, -1],
    [None, 2, 999, 0, 0, 0, -1], [None, 2, 999, 0, 0, 0, -1], [None, 2, 999, 0, 0, 0, -1],
    ["貸倒引当金", 2, 10, 2, 0, 3, -1],
    [None, 2, 999, 0, 0, 0, -1], [None, 2, 999, 0, 0, 0, -1], [None, 2, 999, 0, 0, 0, -1], [None, 2, 999, 0, 0, 0, -1],
    [None, 2, 999, 0, 0, 0, -1], [None, 2, 999, 0, 0, 0, -1], [None, 2, 999, 0, 0, 0, -1],
    ["事業主借", 2, 40, 3, 1, 5, 1], ["元入金", 2, 40, 3, 1, 6, 1],
    ["青色申告特別控除前の所得金額", 2, 70, 3, 2, 9, 1], ["合計", 2, 120, 0, 0, -8, 1],
]

PL_FREE_EXPENSE = list(range(24, 30))   # ㉕〜㉚
PL_FREE_MODOSHI = [34, 35]              # ㉟㊱(繰戻額等)
PL_FREE_KURIIRE = [39, 40]              # ㊵㊶(繰入額等)
PL_ZAPPI = 30                           # ㉛雑費
BS_FREE_ASSET = list(range(16, 23))     # 資産の空欄 7 行
BS_FREE_LIAB_UPPER = list(range(31, 38))  # 負債の空欄(貸倒引当金より上) 7 行
BS_FREE_LIAB_LOWER = list(range(39, 46))  # 負債の空欄(貸倒引当金より下) 7 行
BS_KASHIDAORE = 38

# 貸借対照表の固定行: 名前のゆれ → CL2 の番号(資産側 / 負債・資本側)
BS_ALIAS_ASSET = {
    "現金": 0, "当座預金": 1, "定期預金": 2, "その他の預金": 3, "その他預金": 3, "受取手形": 4, "売掛金": 5,
    "有価証券": 6, "棚卸資産": 7, "たな卸資産": 7, "前払金": 8, "前払い金": 8, "貸付金": 9, "建物": 10,
    "建物附属設備": 11, "建物付属設備": 11, "機械装置": 12, "車両運搬具": 13, "車輌運搬具": 13, "車輛運搬具": 13,
    "工具器具備品": 14, "土地": 15, "事業主貸": 23, "合計": 24, "資産の部合計": 24, "資産合計": 24,
}
BS_ALIAS_LIAB = {
    "支払手形": 25, "買掛金": 26, "借入金": 27, "未払金": 28, "前受金": 29, "預り金": 30, "貸倒引当金": 38,
    "事業主借": 46, "元入金": 47, "青色申告特別控除前の所得金額": 48, "合計": 49, "負債資本の部合計": 49,
}

JSON_SCHEMA = {
    "name": "KojinAoiro",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["様式", "決算日", "PL", "BS", "雑収入"],
        "properties": {
            "様式": {"type": "string", "enum": ["青色申告決算書(一般用)", "青色申告決算書(不動産所得用)",
                                                "青色申告決算書(農業所得用)", "収支内訳書", "不明"]},
            "決算日": {"type": "string"},
            "PL": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["欄番号", "科目名", "金額", "page_no"],
                "properties": {"欄番号": {"type": "integer"}, "科目名": {"type": "string"},
                               "金額": {"anyOf": [{"type": "number"}, {"type": "string"}]}, "page_no": {"type": "integer"}}}},
            "BS": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["区分", "科目名", "期末金額", "page_no"],
                "properties": {"区分": {"type": "string", "enum": ["資産", "負債・資本"]}, "科目名": {"type": "string"},
                               "期末金額": {"anyOf": [{"type": "number"}, {"type": "string"}]}, "page_no": {"type": "integer"}}}},
            "雑収入": {"type": "object", "additionalProperties": False, "required": ["金額", "page_no"],
                    "properties": {"金額": {"anyOf": [{"type": "number"}, {"type": "string"}]}, "page_no": {"type": "integer"}}},
        },
    },
}


def _nm(s):
    s = unicodedata.normalize("NFKC", str(s or ""))
    return re.sub(r"[\s・()\[\]（）「」]", "", s)


def read_with_openai(image_paths, api_key, model):
    """ページ画像を OpenAI に送り、欄番号つきで読ませる"""
    from openai import OpenAI
    with open(PROMPT_PATH, "r", encoding="utf-8") as f:
        prompt = f.read()
    content = [{"type": "text", "text": prompt}]
    for i, p in enumerate(image_paths[:10]):
        mime = "image/png" if p.lower().endswith(".png") else "image/jpeg"
        with open(p, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        content.append({"type": "text", "text": "%dページ目" % (i + 1)})
        content.append({"type": "image_url", "image_url": {"url": "data:%s;base64,%s" % (mime, b64), "detail": "high"}})
    client = OpenAI(api_key=api_key)
    last = None
    for _ in range(3):
        try:
            resp = client.chat.completions.create(
                model=model, temperature=0,
                messages=[{"role": "user", "content": content}],
                response_format={"type": "json_schema", "json_schema": JSON_SCHEMA},
            )
            return json.loads(resp.choices[0].message.content)
        except Exception as e:
            last = e
    raise RuntimeError("個人の読み取りに失敗しました: %s" % last)


def _base_cand(row):
    return {"order": row[1], "family": row[2], "genus": row[3], "species": row[4], "variety": row[5],
            "property": row[6], "variety_name": row[0] if row[0] is not None else None}


def _free_candidates(name, kind, i):
    """空欄行の照合(engine2.py 6340-6385 / 6540-6575 の写し)

    kind: "pl" または "bs"。i は CL1 / CL2 での番号。
    """
    close = master.search_variety_title_syou(name)
    for xi, xc in enumerate(close):
        if xc in ("車両費", "車輛費", "車輌費"):
            close[0], close[xi] = close[xi], close[0]
    cands = []
    for c in close:
        code = master.search_variety_code(c, code=(1, 4), between=(1, 35)) if kind == "pl" else master.search_variety_code(c, code=(2,))
        if not code or len(code) < 6:
            continue
        if code[0] == 2 and code[1] == 40 and code[2] == 2 and code[3] == 0 and code[4] == 29:
            sub = {"order": 2, "family": 50, "genus": 0, "species": 5, "variety": 1, "property": 1, "variety_name": "長期借入金"}
        else:
            sub = {"order": code[0], "family": code[1], "genus": code[2], "species": code[3], "variety": code[4],
                   "property": code[5], "variety_name": c}
        if kind == "pl":
            if i in PL_FREE_EXPENSE:
                if code[0] == 1 and code[1] in (2, 4, 7, 10):
                    if c == "支払手数料":
                        cands.append({"order": 1, "family": 4, "genus": 2, "species": 18, "variety": 1, "property": -1, "variety_name": c})
                    elif code[1] == 4:
                        cands.append(sub)
            elif i in PL_FREE_MODOSHI:
                # [pmj-ana] 元のエンジンは family 4/7/10 を候補にするが、make.do がこの行で family 6/9 だけを残すため
                #           候補が必ず空になっていた(科目コードが "____" になる不具合)。make.do に合わせて 6/9 にする
                if code[0] == 1 and code[1] in (6, 9):
                    cands.append(sub)
            elif i in PL_FREE_KURIIRE:
                # [pmj-ana] make.do がこの行で残す family 7/10 に合わせる(元のエンジンは 4/7/10)
                if code[0] == 1 and code[1] in (7, 10):
                    cands.append(sub)
        else:
            if i < 25:
                if code[0] == 2 and code[1] in (10, 20, 30):
                    cands.append(sub)
            elif i > 25:
                if code[0] == 2 and code[1] >= 40:
                    cands.append(sub)
    return cands


def _row(candidate, kotei, amount, page, tabindex, realtext=None):
    r = {
        "candidate": candidate,
        "amount_pre_year": "",                  # 個人は今期だけ(元のエンジンと同じ)
        "amount_this_year": amount,
        "db_exist": "1.00",
        "start_x": 0, "start_y": 0, "end_x": 0, "end_y": 0,
        "tabindex": tabindex,
        "page": page,
        "kotei": kotei,
    }
    if realtext is not None:
        r["realtext"] = realtext
    return r


def _add_amount(a, b):
    """金額の文字列を足す(雑費への加算用)"""
    if a == "" and b == "":
        return ""
    return str(int(a or 0) + int(b or 0))


def build_detail(data):
    """読み取り結果 → (96 行, 決算日, ログ)。対象外の様式なら ValueError"""
    form = data.get("様式", "")
    if form != SUPPORTED_FORM:
        raise ValueError("対象外の様式です: %s(一般用の青色申告決算書のみ対応)" % (form or "不明"))
    log = []

    # ---- 損益計算書: 欄番号 → 位置 ----
    pl_amount = [""] * 45
    pl_page = [0] * 45
    pl_name = [None] * 45
    overflow = []
    for it in data.get("PL", []) or []:
        no = it.get("欄番号")
        amt = to_amount(it.get("金額"))
        name = str(it.get("科目名") or "").strip()
        page = max(int(it.get("page_no") or 1) - 1, 0)
        if isinstance(no, int) and 1 <= no <= 45:
            i = no - 1
            if pl_amount[i] != "" and i in PL_FREE_EXPENSE:
                overflow.append((name, amt, page))          # 同じ空欄行が2回来たら、あふれとして扱う
                continue
            pl_amount[i], pl_page[i] = amt, page
            if CL1[i][2] == 999:
                pl_name[i] = name
        else:
            overflow.append((name, amt, page))
    # 欄番号が無い経費: 空いている空欄行へ → それも無ければ雑費に足す
    for name, amt, page in overflow:
        free = [i for i in PL_FREE_EXPENSE if pl_amount[i] == "" and not pl_name[i]]
        if free:
            i = free[0]
            pl_amount[i], pl_page[i], pl_name[i] = amt, page, name
        else:
            pl_amount[PL_ZAPPI] = _add_amount(pl_amount[PL_ZAPPI], amt)
            log.append("空欄の経費があふれたため雑費に加算: %s %s" % (name, amt))

    rows = []
    for i, base in enumerate(CL1):
        if base[2] == 999 and pl_name[i]:
            cands = _free_candidates(pl_name[i], "pl", i)
            if not cands:
                cands = [_base_cand(base)]
            rows.append(_row(cands, "m1_%d" % i, pl_amount[i], pl_page[i], 2, realtext=pl_name[i]))
        else:
            kotei = ("m1_%d" if base[2] == 999 else "kotei_1_%d") % i
            rows.append(_row([_base_cand(base)], kotei, pl_amount[i], pl_page[i], 2))

    # ---- 貸借対照表 ----
    bs_amount = [""] * 50
    bs_page = [0] * 50
    bs_name = [None] * 50
    seen_kashidaore = False
    for it in data.get("BS", []) or []:
        side = it.get("区分")
        name = str(it.get("科目名") or "").strip()
        amt = to_amount(it.get("期末金額"))
        page = max(int(it.get("page_no") or 1) - 1, 0)
        key = _nm(name)
        if side == "資産":
            j = BS_ALIAS_ASSET.get(key)
            slots = BS_FREE_ASSET
        else:
            j = BS_ALIAS_LIAB.get(key)
            if j == BS_KASHIDAORE:
                seen_kashidaore = True
            slots = BS_FREE_LIAB_LOWER if seen_kashidaore else BS_FREE_LIAB_UPPER
        if j is not None:
            bs_amount[j], bs_page[j] = amt, page
            continue
        free = [k for k in slots if bs_amount[k] == "" and not bs_name[k]]
        if free:
            k = free[0]
            bs_amount[k], bs_page[k], bs_name[k] = amt, page, name
        else:
            log.append("貸借対照表の空欄行があふれたため除外: %s %s %s" % (side, name, amt))

    for j, base in enumerate(CL2):
        if base[2] == 999 and bs_name[j]:
            cands = _free_candidates(bs_name[j], "bs", j)
            if not cands:
                cands = [_base_cand(base)]
            rows.append(_row(cands, "m2_%d" % j, bs_amount[j], bs_page[j], 1, realtext=bs_name[j]))
        else:
            kotei = ("m2_%d" if base[2] == 999 else "kotei_2_%d") % j
            rows.append(_row([_base_cand(base)], kotei, bs_amount[j], bs_page[j], 1))

    # ---- 雑収入(engine2.py 6632-6684) ----
    z = data.get("雑収入") or {}
    rows.append(_row([{"order": 1, "family": 1, "genus": 0, "species": 0, "variety": 64, "property": 1, "variety_name": "雑収入"}],
                     "kotei_3_0", to_amount(z.get("金額")), max(int(z.get("page_no") or 1) - 1, 0), 3))

    if len(rows) != 96:
        raise RuntimeError("個人の行数が 96 ではありません: %d" % len(rows))
    return rows, data.get("決算日", ""), log
