# -*- coding: utf-8 -*-
"""法人: ana の読み取り結果 → v2ac(iTaskScanPapers2)と同じ detail 行

ana(callgpt.py)の結果:
  {"BS":[{勘定科目, 今期{金額,page_no}, 前期{...}, 前々期{...}, type, 分類}, ...], "PL":[...], "販売費":[...], "製造原価":[...]}
v2ac の detail 行(make.do の後処理がそのまま使える形):
  {candidate:[{order,family,genus,species,variety,property,variety_name}], amount_pre_year, amount_this_year,
   db_exist, page(0始まり), start_x/start_y/end_x/end_y(=0), tabindex(1資産 2負債純資産 3損益 4販管費), val, realtext}

  ・並び順は ana の順(タブごとにまとめる: 資産 → 負債純資産 → 損益 → 販管費)
  ・製造原価は除外(v2ac も製造原価報告書は出力しない)
  ・全期の金額が空の行は除外(ana の get_ai_response.php と同じ)
  ・照合範囲は ana の type / 分類 から決める(分類が分からなければ BS 全体 / PL 全体)
"""
import re
import unicodedata

from pmjana import master

# ana の分類(aitext1_1 の BS/PL 分類一覧) → マスタの範囲 (code, between)
#   between が空なら code の前方一致、同じ長さなら数値コードの範囲
#   ※ 上から順に「含まれるか」で判定するので、長い名前を先に置く(例: 純資産合計 を 資産合計 より先に)
BS_SCOPE = [
    ("負債純資産合計", (2, 120), ()), ("純資産合計", (2, 110), ()),
    ("有形固定資産", (2, 20, 1), ()), ("無形固定資産", (2, 20, 2), ()), ("投資その他の資産", (2, 20, 3), ()),
    ("流動資産", (2, 10), ()), ("固定資産", (2, 20), ()), ("繰延資産", (2, 30), ()), ("資産合計", (2, 35), ()),
    ("流動負債", (2, 40), ()), ("固定負債", (2, 50), ()), ("負債合計", (2, 60), ()),
    ("資本金", (2, 70, 1), ()), ("資本準備金", (2, 70, 2), ()), ("資本剰余金", (2, 70, 2), ()),
    ("繰越利益剰余金", (2, 70, 3), ()), ("利益剰余金", (2, 70, 3), ()), ("自己株式", (2, 70, 6), ()),
    ("株主資本", (2, 70), ()), ("評価・換算差額等", (2, 80), ()), ("評価換算差額等", (2, 80), ()),
    ("新株予約権", (2, 90), ()), ("非支配株主持分", (2, 100), ()),
]
PL_SCOPE = [
    ("売上総利益", (1, 3), ()), ("売上高", (1, 1), ()), ("売上原価", (1, 2), ()), ("販売費及び一般管理費", (1, 4), ()),
    ("営業外収益", (1, 6), ()), ("営業外費用", (1, 7), ()), ("営業利益", (1, 5), ()), ("経常利益", (1, 8), ()),
    ("特別利益", (1, 9), ()), ("特別損失", (1, 10), ()), ("税引前", (1, 11), ()), ("法人税", (1, 12), ()),
    ("当期純利益", (1, 13), ()),
]
ASSET = ((2, 10), (2, 35))      # 資産側の範囲
LIAB = ((2, 40), (2, 120))      # 負債・純資産側の範囲
BS_ALL = ((2,), ())
PL_ALL = ((1,), ())
SGA = ((1, 4), ())


def _norm(s):
    s = unicodedata.normalize("NFKC", str(s or ""))
    return re.sub(r"\s+", "", s).replace("(損失)", "").replace("（損失）", "")


def _bs_side(bunrui):
    b = _norm(bunrui)
    if not b:
        return None
    if "負債純資産" in b or "負債" in b or "純資産" in b or "資本" in b or "剰余金" in b or "株式" in b \
            or "新株予約権" in b or "非支配" in b or "評価" in b:
        return "liab"
    if "資産" in b:
        return "asset"
    return None


def _scope_for(sec, bunrui):
    b = _norm(bunrui)
    table = BS_SCOPE if sec == "BS" else PL_SCOPE
    for key, code, between in table:
        if key in b:
            return (code, between)
    return None


def to_amount(v):
    """ana の金額(数値 / 文字列) → make.do に渡す文字列(カンマなし、マイナスは '-')。空は ''"""
    if v is None:
        return ""
    if isinstance(v, bool):
        return ""
    if isinstance(v, (int, float)):
        return str(int(round(v)))
    s = unicodedata.normalize("NFKC", str(v)).strip()
    if s == "":
        return ""
    neg = False
    if s[:1] in ("△", "▲", "-", "−", "ー", "▽", "▼"):
        neg = True
        s = s[1:]
    if s.startswith("(") and s.endswith(")"):
        # 括弧はマイナスではない(ana のプロンプト aitext1_1「括弧付き金額の扱い」と同じ。日本の決算書では
        # 括弧は小計・合計などの表記。マイナスは △ ▲ − で表す)
        s = s[1:-1]
    s = re.sub(r"[,，円\s]", "", s)
    if not re.fullmatch(r"\d+(\.\d+)?", s):
        return ""
    n = int(round(float(s)))
    return str(-n if neg else n)


def _term(item, name):
    t = item.get(name)
    return t if isinstance(t, dict) else {}


def _page(item):
    for name in ("今期", "前期"):
        p = _term(item, name).get("page_no")
        try:
            p = int(str(p).strip())
            if p >= 1:
                return p - 1               # ana は 1 始まり、zaiTask は 0 始まり
        except Exception:
            pass
    return 0


def _row(item, cands, db_exist, tabindex):
    label = str(item.get("勘定科目") or "")
    return {
        "candidate": cands,
        "amount_this_year": to_amount(_term(item, "今期").get("金額")),
        "amount_pre_year": to_amount(_term(item, "前期").get("金額")),
        "db_exist": db_exist,
        "page": _page(item),
        "start_x": 0, "start_y": 0, "end_x": 0, "end_y": 0,     # ana には読み取り位置が無い
        "tabindex": tabindex,
        "val": label,
        "realtext": label,                                     # make.do: 同名の候補を先頭に寄せる
    }


def _fallback(order, family, label):
    """候補が無いとき: make.do が m_kanjo_view から候補を作り直せるよう species/variety を空にする"""
    return [{"order": order, "family": family, "genus": 0, "species": "", "variety": "", "property": 1,
             "variety_name": str(label or "")}]


def build_detail(response):
    """ana の結果 → (detail 行のリスト, ログ)"""
    log = []
    tab1, tab2, tab3, tab4 = [], [], [], []

    for item in response.get("BS", []) or []:
        if not isinstance(item, dict) or _all_empty(item):
            continue
        label = item.get("勘定科目", "")
        side = _bs_side(item.get("分類"))
        scope = _scope_for("BS", item.get("分類"))
        scopes = ([scope] if scope else []) + ([ASSET] if side == "asset" else [LIAB] if side == "liab" else []) + [BS_ALL]
        cands, ratio = master.match(label, scopes)
        if cands:
            if side is None:
                side = "asset" if cands[0]["family"] < 40 else "liab"
        else:
            fam = (scope[0][1] if scope else (10 if side == "asset" else 40 if side == "liab" else 999))
            cands, ratio = _fallback(2, fam, label), "0.00"
            log.append("BS 未照合: %s(分類 %s)" % (label, item.get("分類")))
        (tab1 if side == "asset" else tab2).append(_row(item, cands, ratio, 1 if side == "asset" else 2))

    for sec, tab, scopes_default, tabindex in (("PL", tab3, [PL_ALL], 3), ("販売費", tab4, [SGA, PL_ALL], 4)):
        for item in response.get(sec, []) or []:
            if not isinstance(item, dict) or _all_empty(item):
                continue
            label = item.get("勘定科目", "")
            scope = _scope_for("PL", item.get("分類")) if sec == "PL" else None
            scopes = ([scope] if scope else []) + scopes_default
            cands, ratio = master.match(label, scopes)
            if not cands:
                fam = scope[0][1] if scope else 4
                cands, ratio = _fallback(1, fam, label), "0.00"
                log.append("%s 未照合: %s(分類 %s)" % (sec, label, item.get("分類")))
            tab.append(_row(item, cands, ratio, tabindex))

    skipped = len(response.get("製造原価", []) or [])
    if skipped:
        log.append("製造原価 %d 行は除外" % skipped)
    return tab1 + tab2 + tab3 + tab4, log


def _all_empty(item):
    for name in ("今期", "前期", "前々期"):
        if to_amount(_term(item, name).get("金額")) != "":
            return False
    return True


def normalize_date(s):
    """'2025-03-31' / '2025/3/31' / '2025年3月31日' → '2025/03/31'。読めなければ ''"""
    s = unicodedata.normalize("NFKC", str(s or "")).strip()
    m = re.search(r"(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})", s)
    if not m:
        return ""
    return "%04d/%02d/%02d" % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
