# callgpt_ut.py
import re
from typing import List, Dict, Any, Optional, Tuple, Union
from decimal import Decimal, InvalidOperation
import unicodedata
import copy

def _norm_label(label: str) -> str:
    """
    勘定科目名のゆらぎ吸収用の簡易正規化関数

    - NFKC 正規化（全角→半角、幅の異なる文字の統一）
    - 空白類の削除（全角スペース含む）
    - よくある装飾記号の除去/統一
    """
    if not isinstance(label, str):
        return ""

    s = unicodedata.normalize("NFKC", label)

    # スペース類除去
    for ch in [" ", "　", "\t"]:
        s = s.replace(ch, "")

    # よくある記号を軽く整形（必要に応じて追加してOK）
    s = s.replace("（", "(").replace("）", ")")
    s = s.replace("・", "")
    s = s.replace("　", "")

    return s
def _load_merge_groups(margelist_path: str) -> List[List[str]]:
    """
    margelist ファイルを読み込み、各行を [勘定科目1, 勘定科目2, ...] のリストとして返す。

    - 行数は無制限に読み込む
    - 空行は無視
    - カンマ区切り
    - 行ごとに「マージ対象の勘定科目」を列挙する
      例: 当期純利益,当期利益,当期純損失
    """
    groups: List[List[str]] = []

    try:
        with open(margelist_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                labels = [s.strip() for s in line.split(",") if s.strip()]

                # 1つだけだとマージにならないので 2つ以上のみ採用
                if len(labels) >= 2:
                    # 「行ごと30個まで」の想定だが、特に制限はかけずそのまま採用
                    groups.append(labels)
    except FileNotFoundError:
        # ファイルが無い場合は何もマージしない
        return []

    return groups
def change_kanjyoukamoku_01(all_results: List[Dict[str, Any]], in_place: bool = True) -> List[Dict[str, Any]]:
    """
    勘定科目の名称をページ単位で調整するユーティリティ。

    ルール（本関数の最終仕様）:
      1) 『その他利益剰余金』の各文字がすべて含まれていれば、その勘定科目名を『その他利益剰余金』に統一
      1b) 『うち当期純利益』の各文字がすべて含まれていれば、その勘定科目名を『うち当期純利益』に統一
      1c) 『販売一般管理費』の各文字がすべて含まれていれば、その勘定科目名を『販売費及び一般管理費』に統一
      2) 勘定科目名に含まれるローマ数字（I, II, Ⅲ など）を削除
      3) そのページに「売上高」が既に存在する場合のみ、売上関連の名称調整（既存仕様）
      4) 【重要】「(1)」「（１）」「①」などの“項番”の削除は、『その他』を含む科目名に限る
      5) 勘定科目名に「（重複）」または「(重複)」が含まれるレコードは削除
      6) ページ内に『営業外収益/営業外費用』が存在しない場合のみ、『…合計/計/（合計）』等を素名称へ寄せる
      7) 『分類』が ''（空文字）の item には直前の『分類』をコピー（前方継承）
      8) 末尾が『収入高』なら『収入』に変更（空白無視、前方は保持）
      9) 末尾が『売上収入』なら『売上高』に変更（空白無視、前方は保持）
     10) 【追加】『売上高(〇〇〇)／売上高（〇〇〇）』なら『〇〇〇』に変更（空白無視）
     11) 【追加】『販売費及び一般管理費』『製造原価』について、ページ内に素名称が無い場合でも
         「◯◯（合計）／◯◯合計／◯◯計」を素名称へ寄せる
    """

    # -------------------------------------------------------
    # in_place=False のときは入力を浅くコピーして破壊を避ける
    # -------------------------------------------------------
    if not in_place:
        copied: List[Dict[str, Any]] = []
        for page_data in all_results:
            if isinstance(page_data, dict):
                new_page = dict(page_data)
                km_list = page_data.get("kanjyokamoku", [])
                if isinstance(km_list, list):
                    new_page["kanjyokamoku"] = [
                        dict(item) if isinstance(item, dict) else item for item in km_list
                    ]
                copied.append(new_page)
            else:
                copied.append(page_data)
        work = copied
    else:
        work = all_results

    # -------------------------------------------------------
    # ユーティリティ関数群
    # -------------------------------------------------------
    def _strip_spaces(s: str) -> str:
        """全角/半角スペースをすべて除去。"""
        return re.sub(r'[\u3000\s]+', '', s) if isinstance(s, str) else s

    def _has_all_chars(name: str, target: str) -> bool:
        if not isinstance(name, str):
            return False
        return all(ch in name for ch in target)

    def _remove_roman_numerals(name: str) -> str:
        if not isinstance(name, str):
            return name
        name = re.sub(r'\b[IVXLCDM]+\b', '', name)          # 半角ローマ数字
        name = re.sub(r'[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]+', '', name)     # 全角ローマ数字
        return name

    _LEADING_SECTION_RE = re.compile(
        r"""^\s*(?:[\(\（]\s*[0-9０-９]+\s*[\)\）]|[\u2460-\u2473]|[\u3251-\u325F\u32B1-\u32BF])\s*""",
        re.VERBOSE
    )
    _ANYWHERE_SECTION_RE = re.compile(
        r"""(?:[\(\（]\s*[0-9０-９]+\s*[\)\）])|[\u2460-\u2473\u3251-\u325F\u32B1-\u32BF]"""
    )

    def _remove_section_indexes_if_sonota(name: str) -> str:
        if not isinstance(name, str):
            return name
        if "その他" not in name:
            return name
        s = _LEADING_SECTION_RE.sub('', name)
        s = _ANYWHERE_SECTION_RE.sub('', s)
        s = re.sub(r'[ \t\u3000]+', ' ', s).strip()
        return s

    TARGET_ALLCHARS_NORMALIZE_MAP = [
        ("その他利益剰余金", "その他利益剰余金"),
        ("うち当期純利益", "うち当期純利益"),
        ("販売一般管理費", "販売費及び一般管理費"),
    ]

    _DUP_MARK_RE = re.compile(r'[（(]\s*重複\s*[)）]')
    _OPEN_BRACKETS  = r'\(\（\[\［\{\｛<＜〈《「『【｟〔'
    _CLOSE_BRACKETS = r'\)\）\]\］\}\｝>＞〉》」』】｠〕'

    def _normalize_total_variant_if_base_missing(km_list: List[Dict[str, Any]], base: str) -> None:
        if not isinstance(km_list, list):
            return
        has_base = any(
            isinstance(it, dict) and isinstance(it.get("勘定科目", ""), str) and
            _strip_spaces(it["勘定科目"]) == base
            for it in km_list
        )
        if has_base:
            return
        bracketed_gokei_re = re.compile(
            rf"^{re.escape(base)}[ \u3000]*[{_OPEN_BRACKETS}]\s*合計\s*[{_CLOSE_BRACKETS}]$"
        )
        plain_gokei_re = re.compile(rf"^{re.escape(base)}[ \u3000]*合計$")
        plain_keire    = re.compile(rf"^{re.escape(base)}[ \u3000]*計$")
        for it in km_list:
            if not isinstance(it, dict):
                continue
            name = it.get("勘定科目", "")
            if not isinstance(name, str):
                continue
            s = _strip_spaces(name)
            if bracketed_gokei_re.match(s) or plain_gokei_re.match(s) or plain_keire.match(s):
                it["勘定科目"] = base

    # -------------------------------------------------------
    # メイン処理
    # -------------------------------------------------------
    for page_data in work:
        if not isinstance(page_data, dict):
            continue
        kanjyokamoku_list = page_data.get("kanjyokamoku", [])
        if not isinstance(kanjyokamoku_list, list):
            continue

        # (A0) 『分類』の前方継承
        prev_bunrui = None
        for item in kanjyokamoku_list:
            if not isinstance(item, dict):
                continue
            if item.get("分類", None) == '' and prev_bunrui is not None:
                item["分類"] = prev_bunrui
            curr = item.get("分類", None)
            if isinstance(curr, str) and curr != '':
                prev_bunrui = curr

        # (A) 名称正規化（ローマ数字除去 → 「その他」項番除去）
        for item in kanjyokamoku_list:
            if not isinstance(item, dict):
                continue
            name = item.get("勘定科目", "")
            if not isinstance(name, str):
                continue
            name = _remove_roman_numerals(name)
            name = _remove_section_indexes_if_sonota(name)
            item["勘定科目"] = name

        # (B) 含有統一（各文字すべて含有）
        for item in kanjyokamoku_list:
            if not isinstance(item, dict):
                continue
            name = item.get("勘定科目", "")
            if not isinstance(name, str):
                continue
            for cond, target in TARGET_ALLCHARS_NORMALIZE_MAP:
                if _has_all_chars(name, cond):
                    # 条件文字列に「その他」が無いのに科目名へ「その他」が付いている場合は、
                    # 合計行ではなく内訳行（別科目）なので統合しない。
                    #   例) 販売費及び一般管理費（合計行）
                    #       その他販売費及び一般管理費（内訳の一項目。別科目）
                    # 統合すると同名衝突で後勝ちとなり、合計行の金額が内訳値で失われる。
                    # 条件側に「その他」を含む規則（その他利益剰余金など）は従来どおり動く。
                    if "その他" in name and "その他" not in cond:
                        continue
                    item["勘定科目"] = target
                    break

        # (B-2) 営業外収益/営業外費用の素名称寄せ
        _normalize_total_variant_if_base_missing(kanjyokamoku_list, base="営業外収益")
        _normalize_total_variant_if_base_missing(kanjyokamoku_list, base="営業外費用")
        # ★追加: 販売費及び一般管理費 / 製造原価 も「◯◯（合計）」「◯◯合計」「◯◯計」を素名称へ寄せる
        _normalize_total_variant_if_base_missing(kanjyokamoku_list, base="販売費及び一般管理費")
        _normalize_total_variant_if_base_missing(kanjyokamoku_list, base="製造原価")

        # (B-3) 末尾『収入高』→『収入』（空白無視、前方保持）
        for item in kanjyokamoku_list:
            if not isinstance(item, dict):
                continue
            name = item.get("勘定科目", "")
            if not isinstance(name, str):
                continue
            s = _strip_spaces(name)
            if s.endswith("収入高"):
                new_s = s[:-3] + "収入"
                item["勘定科目"] = new_s  # 空白は除去された形で統一

        # (B-4) 末尾『売上収入』→『売上高』（空白無視、前方保持）
        for item in kanjyokamoku_list:
            if not isinstance(item, dict):
                continue
            name = item.get("勘定科目", "")
            if not isinstance(name, str):
                continue
            s = _strip_spaces(name)
            if s.endswith("売上収入"):
                new_s = s[:-4] + "売上高"
                item["勘定科目"] = new_s  # 空白は除去された形で統一

        # (B-5) 『売上高(〇〇) / 売上高（〇〇）』→『〇〇』（空白無視）
        sell_with_paren_re = re.compile(r'^売上高[（(]\s*(.+?)\s*[)）]$')
        for item in kanjyokamoku_list:
            if not isinstance(item, dict):
                continue
            name = item.get("勘定科目", "")
            if not isinstance(name, str):
                continue
            s = _strip_spaces(name)
            m = sell_with_paren_re.match(s)
            if m:
                inner = m.group(1).strip()
                item["勘定科目"] = inner  # かっこ内の文字列に置換

        # (C) 売上高ロジック（既存）
        has_uriage_taka = any(
            isinstance(item, dict) and isinstance(item.get("勘定科目", ""), str) and
            _strip_spaces(item["勘定科目"]) == "売上高"
            for item in kanjyokamoku_list
        )

        if has_uriage_taka:
            # 売上高の金額セット収集
            uriage_amounts = set()
            for itm in kanjyokamoku_list:
                if isinstance(itm, dict) and _strip_spaces(itm.get("勘定科目", "")) == "売上高":
                    for term in ("今期", "前期", "前々期"):
                        if term in itm and isinstance(itm[term], dict):
                            amt = itm[term].get("金額", "")
                            if amt not in ("", None):
                                uriage_amounts.add(str(amt))

            # 隣接 or 同額なら『売上高合計』へ寄せる
            for idx, item in enumerate(kanjyokamoku_list):
                if not isinstance(item, dict):
                    continue
                if item.get("type") != "PL":
                    continue
                name = item.get("勘定科目", "")
                if not isinstance(name, str):
                    continue
                normalized = _strip_spaces(name)
                if ("売上" in normalized) and normalized not in ("売上高", "売上原価", "売上高合計"):
                    neighbor = False
                    if idx > 0:
                        prev = kanjyokamoku_list[idx - 1]
                        if isinstance(prev, dict) and _strip_spaces(prev.get("勘定科目", "")) == "売上高":
                            neighbor = True
                    if idx < len(kanjyokamoku_list) - 1:
                        nxt = kanjyokamoku_list[idx + 1]
                        if isinstance(nxt, dict) and _strip_spaces(nxt.get("勘定科目", "")) == "売上高":
                            neighbor = True

                    same_amount = False
                    for term in ("今期", "前期", "前々期"):
                        if term in item and isinstance(item[term], dict):
                            amt = item[term].get("金額", "")
                            if str(amt) in uriage_amounts:
                                same_amount = True
                                break

                    if neighbor and same_amount:
                        item["勘定科目"] = "売上高合計"

        # (D) 「（重複）」付きレコードの削除
        filtered_list = []
        for item in kanjyokamoku_list:
            if not isinstance(item, dict):
                filtered_list.append(item)
                continue
            name = item.get("勘定科目", "")
            if isinstance(name, str) and _DUP_MARK_RE.search(name):
                continue
            filtered_list.append(item)
        page_data["kanjyokamoku"] = filtered_list

    return work





def change_kanjyoukamoku_03(data: Dict[str, Any], in_place: bool = True) -> Dict[str, Any]:
    """
    目的:
      - PLに「売上高」と「売上高合計(売上高計)」が両方ある場合、各期間の金額/page_noを
        「売上高」→「売上高合計」へコピー（上書き）。
      - 追加(修正): PLに
          ・「流動負債」と「流動負債合計(流動負債計)」
          ・「固定負債」と「固定負債合計(固定負債計)」
        が両方ある場合、各期間の 金額 / page_no を **双方向（空側のみ）** で補完。
      - 勘定科目名の先頭の番号(半角/全角数字、ASCII/Unicodeローマ数字)は無視して判定。
      - 追加: 勘定科目が末尾「損失」の場合、名称を末尾「利益」に変更し、各期間の金額が数値なら符号を反転。
        ※ ただし「特別損失」は除外（名称変更・符号反転の対象外）。
      - ★新規: トップレベル配列が BS/PL/販売費/製造原価 の4本想定。各配列で
        勘定科目が空文字（空白のみ含む）のレコードは削除。

    I/O:
      - { "BS": [...], "PL": [...], "販売費": [...], "製造原価": [...] } を受け取り、同形式で返す。
    """

    PERIODS = ["今期", "前期", "前々期"]
    TOP_KEYS = ("BS", "PL", "販売費", "製造原価")
    # 「損失 → 利益」変換を許可する利益科目
    ALLOWED_PROFIT_LABELS = {
        "売上総利益",
        "売上粗利益",
        "完成工事総利益",
        "兼業事業総利益",
        "営業利益",
        "経常利益",
        "税引前当期純利益",
        "税引前当期利益",
        "法人税等引当前利益",
        "当期純利益",
        "当期利益",
        "税引後純利益",
        "当期未処理利益"
    }



    def _shallow_copy(d: Dict[str, Any]) -> Dict[str, Any]:
        copied: Dict[str, Any] = {}
        # ★ BS/PL/販売費/製造原価 を浅くコピー
        for k in TOP_KEYS:
            lst = d.get(k)
            if isinstance(lst, list):
                copied[k] = [dict(x) if isinstance(x, dict) else x for x in lst]
            elif lst is not None:
                copied[k] = lst
        # 予期せぬ余分キーはそのまま参照（壊したくない場合は必要に応じてコピー化）
        for k, v in d.items():
            if k not in copied:
                copied[k] = v
        return copied

    # 先頭インデックス（見出し番号）除去
    _LEADING_INDEX_RE = re.compile(
        r"""^
            (?:[0-9０-９]+|[IVXLCDMivxlcdm]+|[\u2160-\u217F]+)
            [\s\u3000\.\．,\，:：\-‐–—ｰ\)\]】］）\}、]*
        """,
        re.VERBOSE
    )

    def _strip_leading_index(s: str) -> str:
        if not isinstance(s, str):
            return ""
        t = s.lstrip()
        return _LEADING_INDEX_RE.sub("", t, count=1)

    def _split_prefix_and_core(s: Any) -> (str, str):
        if not isinstance(s, str):
            return "", ""
        left_trimmed = s.lstrip()
        leading_ws = s[:len(s) - len(left_trimmed)]
        m = _LEADING_INDEX_RE.match(left_trimmed)
        if m:
            prefix = leading_ws + m.group(0)
            core = left_trimmed[m.end():]
        else:
            prefix = leading_ws
            core = left_trimmed
        return prefix, core

    def _norm_label(s: Any) -> str:
        """先頭番号無視 + 軽い正規化（判定専用）"""
        if not isinstance(s, str):
            return ""
        t = _strip_leading_index(s)
        t = re.sub(r'[【】\[\]（）\(\)]', '', t)
        t = re.sub(r'[\u3000\s]+', '', t)
        t = t.replace('･', '・').replace('.', '・')
        return t

    def _ensure_period_dict(rec: Dict[str, Any], period: str) -> Dict[str, Any]:
        if period not in rec or not isinstance(rec[period], dict):
            rec[period] = {}
        return rec[period]

    def _has_value(v: Any) -> bool:
        # 0 は有効、None/"" は無効扱い
        return v is not None and not (isinstance(v, str) and v.strip() == "")

    def _copy_missing(src: Dict[str, Any], dst: Dict[str, Any]) -> None:
        """金額/page_no を、src に値があり dst が空のときのみコピー"""
        sa, da = src.get("金額"), dst.get("金額")
        if _has_value(sa) and not _has_value(da):
            dst["金額"] = sa
        sp, dp = src.get("page_no"), dst.get("page_no")
        if _has_value(sp) and not _has_value(dp):
            dst["page_no"] = sp
    def _negate_amount_if_numeric(val):
        """
        数値（int/float or 数字文字列）なら
        「マイナスでない場合だけマイナスにする」。
        すでにマイナスならそのまま。
        それ以外は元の値を返す。
        """
        # 1) 素の数値型
        if isinstance(val, (int, float)):
            return -val if val > 0 else val

        # 2) 文字列の場合
        if isinstance(val, str):
            s = val.strip()

            # すでに先頭にマイナス記号（- or ▲ or △）があるなら何もしない
            if s.startswith(("-", "▲", "△")):
                return val

            # カンマを除いて数値判定
            s_plain = s.replace(",", "")
            try:
                num = float(s_plain)
            except ValueError:
                return val  # 数字でなければそのまま返す

            # プラスならマイナスにする（ここでは "-" を付ける）
            if num > 0:
                # 元のフォーマットをあまり壊したくないなら、頭に "-" を付けるだけ
                return "-" + val
            else:
                return val

        # それ以外の型はそのまま
        return val


    work = data if in_place else _shallow_copy(data)

    # --- ★追加: 各配列で「勘定科目が空」のレコードを削除（空白のみも空とみなす） ---
    def _drop_empty_label_records(lst: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
        if not isinstance(lst, list):
            return lst
        return [
            rec for rec in lst
            if not (isinstance(rec, dict) and isinstance(rec.get("勘定科目", ""), str)
                    and rec.get("勘定科目", "").strip() == "")
        ]

    for k in TOP_KEYS:
        if k in work:
            work[k] = _drop_empty_label_records(work.get(k))

    pl_list: Optional[List[Dict[str, Any]]] = work.get("PL")
    bs_list: Optional[List[Dict[str, Any]]] = work.get("BS")
    # 「販売費」「製造原価」はこの関数では集計/補完対象にしていないが、
    # 空ラベル削除と損失→利益の正規化は下で共通適用する。

    if isinstance(pl_list, list) and pl_list:
        rec_uriage: Optional[Dict[str, Any]] = None
        rec_uriage_total: Optional[Dict[str, Any]] = None

        # ★ 追加: 流動負債 / 流動負債合計(計)
        rec_ryuufusai: Optional[Dict[str, Any]] = None
        rec_ryuufusai_total: Optional[Dict[str, Any]] = None

        # ★ 追加: 固定負債 / 固定負債合計(計)
        rec_koteifusai: Optional[Dict[str, Any]] = None
        rec_koteifusai_total: Optional[Dict[str, Any]] = None

        # レコード特定（先頭番号は無視）
        for rec in pl_list:
            if not isinstance(rec, dict):
                continue
            name = _norm_label(rec.get("勘定科目", ""))

            if name == "売上高":
                rec_uriage = rec
            elif name in ("売上高合計", "売上高計"):
                rec_uriage_total = rec

            elif name == "流動負債":
                rec_ryuufusai = rec
            elif name in ("流動負債合計", "流動負債計"):
                rec_ryuufusai_total = rec

            elif name == "固定負債":
                rec_koteifusai = rec
            elif name in ("固定負債合計", "固定負債計"):
                rec_koteifusai_total = rec

        # 売上高 → 売上高合計（従来仕様：上書きコピー）
        if rec_uriage is not None and rec_uriage_total is not None:
            for p in PERIODS:
                src = rec_uriage.get(p, {})
                dst = _ensure_period_dict(rec_uriage_total, p)
                if isinstance(src, dict):
                    dst["金額"] = src.get("金額")
                    dst["page_no"] = src.get("page_no")
                else:
                    dst["金額"] = None
                    dst["page_no"] = None

        # ★ 流動負債 ↔ 流動負債合計：双方向で空側埋め
        if rec_ryuufusai is not None and rec_ryuufusai_total is not None:
            for p in PERIODS:
                base = _ensure_period_dict(rec_ryuufusai, p)
                tot  = _ensure_period_dict(rec_ryuufusai_total, p)
                _copy_missing(base, tot)   # 流動負債 → 合計
                _copy_missing(tot, base)   # 合計 → 流動負債

        # ★ 固定負債 ↔ 固定負債合計：双方向で空側埋め
        if rec_koteifusai is not None and rec_koteifusai_total is not None:
            for p in PERIODS:
                base = _ensure_period_dict(rec_koteifusai, p)
                tot  = _ensure_period_dict(rec_koteifusai_total, p)
                _copy_missing(base, tot)   # 固定負債 → 合計
                _copy_missing(tot, base)   # 合計 → 固定負債

    if isinstance(bs_list, list) and bs_list:

        rec_kotei: Optional[Dict[str, Any]] = None
        rec_kotei_total: Optional[Dict[str, Any]] = None
        rec_kurikoshi: Optional[Dict[str, Any]] = None
        rec_sonota: Optional[Dict[str, Any]] = None

        def _is_koteishisan_total(normalized: str) -> bool:
            return ("固定資産" in normalized) and (("合計" in normalized) or ("計" in normalized))

        # レコード特定
        for rec in bs_list:
            if not isinstance(rec, dict):
                continue
            name = _norm_label(rec.get("勘定科目", ""))

            if name == "固定資産":
                rec_kotei = rec
            elif _is_koteishisan_total(name):
                rec_kotei_total = rec
            elif name == "繰越利益剰余金":
                rec_kurikoshi = rec
            elif name == "その他利益剰余金":
                rec_sonota = rec

        # 固定資産 ↔ 固定資産合計：双方向で空側埋め（従来どおり）
        if rec_kotei is not None and rec_kotei_total is not None:
            for p in PERIODS:
                k = _ensure_period_dict(rec_kotei, p)
                t = _ensure_period_dict(rec_kotei_total, p)
                _copy_missing(k, t)  # 固定資産 → 合計
                _copy_missing(t, k)  # 合計 → 固定資産

        # 繰越利益剰余金 ↔ その他利益剰余金（必要なら有効化）
        # if rec_kurikoshi is not None and rec_sonota is not None:
        #     for p in PERIODS:
        #         kr = _ensure_period_dict(rec_kurikoshi, p)
        #         so = _ensure_period_dict(rec_sonota, p)
        #         _copy_missing(kr, so)
        #         _copy_missing(so, kr)
    def _make_negative_if_positive(amount: Any) -> Any:
        """
        数値の場合に「もしマイナスではない場合マイナスにする」ヘルパー。
        例:
          100  -> -100
          -50  -> -50 (そのまま)
          0    -> -0 (実質 0)
          数値以外 -> そのまま返す
        """
        if isinstance(amount, (int, float)):
            if amount > 0:
                return -amount
            # 0 以下はそのまま
            return amount
        return amount
    def _process_loss_to_profit(records: Optional[List[Dict[str, Any]]]) -> None:
        """
        「～損失」系の勘定科目を対応する「～利益」に変換し、
        金額を「もしマイナスではない場合マイナスにする」に変更する。

        対象となるラベルの例：
          - 営業損失
          - 営業損失（△）
          - 営業損失△
          - 当期純損失（△）
          - 経常損失（△） など

        全角/半角括弧や △ を末尾に含んでいても判定対象にする。
        """
        if not isinstance(records, list):
            return

        for rec in records:
            if not isinstance(rec, dict):
                continue

            raw_label = rec.get("勘定科目", "")
            if not isinstance(raw_label, str):
                continue

            # 項番などを分離（例: "① 営業損失（△）" → prefix="① ", core="営業損失（△）"）
            prefix, core = _split_prefix_and_core(raw_label)
            if not core:
                continue

            # core を「(損失部分) + (末尾の装飾)」に分解する
            #
            # base : 「～損失」までの部分
            # suffix : その後ろに付いている括弧や △ など（あれば）
            #
            # 対応例：
            #   "営業損失"         -> base="営業損失", suffix=""
            #   "営業損失（△）"   -> base="営業損失", suffix="（△）"
            #   "営業損失△"       -> base="営業損失", suffix="△"
            #   "当期純損失（△）" -> base="当期純損失", suffix="（△）"
            m = re.match(
                r'^(?P<base>.*損失)'
                r'(?P<suffix>\s*(?:[（(][^（）()]*[）)]\s*|[△▲]\s*)*)$',
                core
            )
            if not m:
                # 末尾が「損失」ではない（括弧や△を含めて見ても「～損失」で終わらない）場合は対象外
                continue

            base_core = m.group("base")      # 例: "営業損失" / "当期純損失"
            suffix = m.group("suffix") or "" # 例: "（△）" / "△" / ""

            # base_core の末尾「損失」を「利益」に変える
            # 例: "営業損失" → "営業利益"
            profit_base_core = re.sub(r'損失$', '利益', base_core)

            # ALLOWED_PROFIT_LABELS との突き合わせは「装飾なし」で行う
            # 例: "① 営業損失（△）" → prefix="① ", profit_base_core="営業利益"
            #      → _norm_label("① 営業利益") = "営業利益" を想定
            candidate_label = _norm_label(f"{prefix}{profit_base_core}")

            # 「営業利益」「売上総利益」「経常利益」「税引前当期利益」「当期利益」
            # 以外は一切変更しない（名前も金額もそのまま）
            if candidate_label not in ALLOWED_PROFIT_LABELS:
                continue

            # ここまで来たら「～損失」→対応する「～利益」に変換してよい
            # 装飾（suffix）はそのまま残して「営業利益（△）」のようにする
            new_core = profit_base_core + suffix
            new_label = f"{prefix}{new_core}"

            # 各期間の金額が数値なら「もしマイナスではない場合マイナスにする」
            for p in PERIODS:
                v = rec.get(p)
                if isinstance(v, dict) and "金額" in v:
                    v["金額"] = _make_negative_if_positive(v.get("金額"))

            # 勘定科目名を「～利益（元の装飾付き）」に更新
            rec["勘定科目"] = new_label



    # ★ BS/PL/販売費/製造原価に適用
    for k in TOP_KEYS:
        _process_loss_to_profit(work.get(k))

    return work

def change_kanjyoukamoku_04(data: Dict[str, Any], in_place: bool = True) -> Dict[str, Any]:
    """
    目的:
      - BSの「負債の部合計」「負債・純資産の部合計（負債純資産合計）」「純資産の部合計（純資産合計）」を認識し、
        期間ごとに 差額 = (負債・純資産の部合計) - (負債の部合計) を計算。
      - 純資産合計（純資産の部計）が差額と不一致で、かつ
        “差額金額の3文字目〜末尾-1（カンマ「,」「，」は無視）” が “純資産金額（全文字列。カンマ無視）” に含まれる
        場合、純資産合計を差額に補正。
      - 株主資本が存在する場合は、純資産合計の補正後金額で同期（該当期間のみ）。
      - 何をどう直したかを print で詳細に表示。

    I/O:
      - { "BS": [...], "PL": [...], "販売費": [...], "製造原価": [...] } を受け取り、同形式で返す
        （処理対象は **BS 配列**）。
    """
    Number = Union[int, float]
    PERIODS: List[str] = ["今期", "前期", "前々期"]

    def _shallow_copy(d: Dict[str, Any]) -> Dict[str, Any]:
        copied: Dict[str, Any] = {}
        for k in ("BS", "PL", "販売費", "製造原価"):
            lst = d.get(k)
            if isinstance(lst, list):
                copied[k] = [dict(x) if isinstance(x, dict) else x for x in lst]
            else:
                copied[k] = lst
        # 上記以外のキーがもしあれば、そのままぶら下げる
        for k, v in d.items():
            if k not in copied:
                copied[k] = v
        return copied

    # ---------- 正規化/数値化ヘルパ ----------
    def _strip_spaces(s: str) -> str:
        return re.sub(r'[\u3000\s]+', '', s) if isinstance(s, str) else s

    def _norm_label(s: Any) -> str:
        if not isinstance(s, str):
            return ""
        t = s
        t = re.sub(r'[【】\[\]（）\(\)]', '', t)
        t = _strip_spaces(t)
        t = t.replace('及び', '・').replace('および', '・').replace('并び', '・')
        t = t.replace('･', '・').replace('.', '・').replace('・の部', 'の部')
        return t

    def _to_number(x: Any) -> Optional[Number]:
        if x is None:
            return None
        if isinstance(x, (int, float)):
            return x
        if isinstance(x, str):
            s = x.strip()
            if s == "":
                return None
            try:
                import unicodedata
                s = unicodedata.normalize('NFKC', s)
            except Exception:
                pass
            s = s.replace(',', '').replace('，', '')
            s = re.sub(r'[−－–—]', '-', s)
            if re.fullmatch(r'-?\d+(\.\d+)?', s):
                return int(s) if '.' not in s else float(s)
        return None

    def _get_amount(rec: Dict[str, Any], period: str) -> Optional[Number]:
        v = rec.get(period, {})
        if isinstance(v, dict):
            return _to_number(v.get("金額"))
        return _to_number(v)

    def _set_amount(rec: Dict[str, Any], period: str, value: Number) -> None:
        if period not in rec or not isinstance(rec[period], dict):
            rec[period] = {"金額": value, "page_no": ""}
        else:
            rec[period]["金額"] = value

    def _money_to_str_no_commas(x: Number) -> str:
        s = f"{x}"
        return s.replace(',', '').replace('，', '')

    def _mid_slice_for_diff(x: Number) -> str:
        s = _money_to_str_no_commas(x)
        if len(s) < 3:
            return ""
        return s[2:-1]

    # ---------- パターン（BS 内で探す） ----------
    pat_fusai = [
        re.compile(r'^負債(?:の部)?(?:合計|計)?$', re.U),
        re.compile(r'^負債合計$', re.U),
    ]
    pat_fusai_jyun = [
        re.compile(r'(?:計)?負債[・･]純資産(?:の部)?(?:計|合計)?$', re.U),
        re.compile(r'(?:負債及び純資産|負債純資産)(?:の部)?(?:計|合計)?$', re.U),
        re.compile(r'^負債純資産合計$', re.U),
    ]
    pat_jyunshisan = [
        re.compile(r'^純資産(?:の部)?(?:計|合計)?$', re.U),
        re.compile(r'^純資産合計$', re.U),
    ]
    pat_kabunushi = [
        re.compile(r'^株主資本$', re.U),
    ]

    def _find_first(items: List[Dict[str, Any]], pats: List[re.Pattern]) -> Optional[Dict[str, Any]]:
        """BS配列から、勘定科目 or 分類 を `_norm_label` 後に照合して最初に一致するものを返す"""
        for it in items:
            if not isinstance(it, dict):
                continue
            # BSのみを対象（type が無い場合も許容）
            tp = it.get("type")
            if tp and tp != "BS":
                continue
            name = _norm_label(it.get("勘定科目", ""))
            bunrui = _norm_label(it.get("分類", ""))
            for p in pats:
                if p.search(name) or p.search(bunrui):
                    return it
        return None

    work = data if in_place else _shallow_copy(data)

    bs_items: Optional[List[Dict[str, Any]]] = work.get("BS")
    if not isinstance(bs_items, list) or not bs_items:
        return work

    # 主要3項目＋任意の株主資本を特定（BS内）
    rec_fusai      = _find_first(bs_items, pat_fusai)
    rec_fusai_jyun = _find_first(bs_items, pat_fusai_jyun)
    rec_jyun       = _find_first(bs_items, pat_jyunshisan)
    rec_kabu       = _find_first(bs_items, pat_kabunushi)

    # 必須3つが揃わなければ何もしない
    if not (rec_fusai and rec_fusai_jyun and rec_jyun):
        return work

    for period in PERIODS:
        amt_fusai = _get_amount(rec_fusai, period)
        amt_fj    = _get_amount(rec_fusai_jyun, period)
        amt_jyun  = _get_amount(rec_jyun, period)

        if amt_fusai is None or amt_fj is None:
            continue

        print(
            f"[BS] 期間={period} "
            f"負債={amt_fusai} / 負債・純資産={amt_fj} / 純資産(現状)={amt_jyun}"
        )
        if rec_kabu is not None:
            print(f"  株主資本(現状)={_get_amount(rec_kabu, period)}")
        else:
            print("  株主資本なし")

        diff = amt_fj - amt_fusai  # 差額 = (負債+純資産) - 負債

        # 純資産が取得でき、かつ差額と不一致 → “含まれる”条件で補正
        if amt_jyun is not None and diff != amt_jyun:
            mid = _mid_slice_for_diff(diff)
            jyun_str = _money_to_str_no_commas(amt_jyun)
            if mid and (mid in jyun_str):
                before = amt_jyun
                _set_amount(rec_jyun, period, diff)
                print(
                    f"  → 純資産補正: 期間={period} "
                    f"{before} → 差額 {diff} "
                    f"(根拠: 差額の3文字目〜末尾-1='{mid}' が 純資産='{jyun_str}' に含まれる)"
                )
                amt_jyun = diff  # 後続同期のため更新

        # 株主資本が存在する場合は、補正後の純資産合計と同期
        if rec_kabu is not None and amt_jyun is not None:
            kabu_amt = _get_amount(rec_kabu, period)
            if kabu_amt != amt_jyun:
                aaa=999
#                _set_amount(rec_kabu, period, amt_jyun)
#                print(
#                    f"  → 株主資本を純資産に同期: 期間={period} "
#                    f"{kabu_amt} → {amt_jyun}"
#                )

    return work

    
def change_kanjyoukamoku_05(data: Dict[str, Any], in_place: bool = True) -> Dict[str, Any]:
    """
    目的:
      - 期間ごとに以下を確認し、売上総利益（粗利益）を自動補完する。
        条件: その期間で
          * 売上総利益の金額が「未設定/欠損」で、
          * 売上高と売上原価の金額が両方とも取得できる
        場合に、
          売上総利益 = 売上高 － 売上原価
        をセットする。
      - 何をどう直したかを print で詳細に表示。

    I/O:
      - { "BS": [...], "PL": [...], "販売費": [...], "製造原価": [...] } を受け取り、同形式で返す
        （処理対象は PL 配列。売上原価の探索は PL と 製造原価 の両方を見る）。
    """
    Number = Union[int, float]
    PERIODS: List[str] = ["今期", "前期", "前々期"]

    def _shallow_copy(d: Dict[str, Any]) -> Dict[str, Any]:
        copied: Dict[str, Any] = {}
        for k in ("BS", "PL", "販売費", "製造原価"):
            lst = d.get(k)
            if isinstance(lst, list):
                copied[k] = [dict(x) if isinstance(x, dict) else x for x in lst]
            else:
                copied[k] = lst
        # ほかのキーがあればそのまま保持
        for k, v in d.items():
            if k not in copied:
                copied[k] = v
        return copied

    # ---------- 正規化/数値化ヘルパ ----------
    def _strip_spaces(s: str) -> str:
        return re.sub(r'[\u3000\s]+', '', s) if isinstance(s, str) else s

    def _norm_label(s: Any) -> str:
        if not isinstance(s, str):
            return ""
        t = s
        t = re.sub(r'[【】\[\]（）\(\)]', '', t)
        t = _strip_spaces(t)
        t = t.replace('及び', '・').replace('および', '・').replace('并び', '・')
        t = t.replace('･', '・').replace('.', '・').replace('・の部', 'の部')
        return t

    def _to_number(x: Any) -> Optional[Number]:
        if x is None:
            return None
        if isinstance(x, (int, float)):
            return x
        if isinstance(x, str):
            s = x.strip()
            if s == "":
                return None
            try:
                import unicodedata
                s = unicodedata.normalize('NFKC', s)
            except Exception:
                pass
            s = s.replace(',', '').replace('，', '')
            s = re.sub(r'[−－–—]', '-', s)
            if re.fullmatch(r'-?\d+(\.\d+)?', s):
                return int(s) if '.' not in s else float(s)
        return None

    def _get_amount(rec: Dict[str, Any], period: str) -> Optional[Number]:
        v = rec.get(period, {})
        if isinstance(v, dict):
            return _to_number(v.get("金額"))
        return _to_number(v)

    def _set_amount(rec: Dict[str, Any], period: str, value: Number) -> None:
        if period not in rec or not isinstance(rec[period], dict):
            rec[period] = {"金額": value, "page_no": ""}
        else:
            rec[period]["金額"] = value

    # ---------- パターン（PL / 製造原価 内で探す） ----------
    # できるだけ素直な表記に限定（必要なら後でパターン追加）
    pat_uriage = [
        re.compile(r'^売上高$', re.U),
        re.compile(r'^売上収益$', re.U),     # IFRS系で稀に
    ]
    pat_genka = [
        re.compile(r'^売上原価$', re.U),
    ]
    pat_gross = [
        re.compile(r'^売上総利益$', re.U),
        re.compile(r'^粗利益$', re.U),
    ]

    def _find_first(items: List[Dict[str, Any]], pats: List[re.Pattern]) -> Optional[Dict[str, Any]]:
        """
        配列から、勘定科目 or 分類 を `_norm_label` 後に照合して最初に一致するものを返す。
        - PL優先の場面では type が "PL" を許容、製造原価では type は気にしない。
        """
        for it in items:
            if not isinstance(it, dict):
                continue
            name = _norm_label(it.get("勘定科目", ""))
            bunrui = _norm_label(it.get("分類", ""))
            for p in pats:
                if p.search(name) or p.search(bunrui):
                    return it
        return None

    work = data if in_place else _shallow_copy(data)

    pl_items: Optional[List[Dict[str, Any]]] = work.get("PL")
    genka_items: List[Dict[str, Any]] = []
    if isinstance(work.get("製造原価"), list):
        genka_items = [x for x in work.get("製造原価") if isinstance(x, dict)]

    if not isinstance(pl_items, list) or not pl_items:
        return work

    # 必要3科目を特定
    # - 売上高 / 売上総利益 は PL から探す
    rec_uriage = _find_first(pl_items, pat_uriage)
    rec_gross  = _find_first(pl_items, pat_gross)
    # - 売上原価 は PL → 見つからなければ 製造原価 でも探す
    rec_genka = _find_first(pl_items, pat_genka)
    if rec_genka is None and genka_items:
        rec_genka = _find_first(genka_items, pat_genka)

    # いずれか欠ける場合は処理しない（厳しめ）
    if not (rec_uriage and rec_genka and rec_gross):
        return work

    for period in PERIODS:
        amt_u  = _get_amount(rec_uriage, period)
        amt_g  = _get_amount(rec_genka, period)
        amt_gp = _get_amount(rec_gross, period)

        print(f"[PL] 期間={period} 売上高={amt_u} / 売上原価={amt_g} / 売上総利益(現状)={amt_gp}")

        # 条件: 売上総利益が未設定（None）か空、かつ 売上高＆売上原価が数値で取得できる
        if (amt_gp is None) and (amt_u is not None) and (amt_g is not None):
            gp = amt_u - amt_g
            _set_amount(rec_gross, period, gp)
            print(
                f"  → 売上総利益を自動補完: 期間={period} 0/未設定 → {gp} "
                f"(根拠: 売上高({amt_u}) - 売上原価({amt_g}))"
            )

    return work

def change_kanjyoukamoku_06(data: Dict[str, Any], in_place: bool = True) -> Dict[str, Any]:
    """
    目的:
      △（三角）記号のOCR誤読に起因する集計科目の不整合を、数式で再計算した値で補正する。

    入出力:
      - 入出力ともに { "BS": [...], "PL": [...], "販売費": [...], "製造原価": [...] } 形式を許容
        （「販売費」「製造原価」は無くてもよい）
      - in_place=True なら参照そのものを更新、False なら浅いコピーを返す

    対象科目と比較ロジック:
      【BS】
        資産の部合計 ＝ 流動資産 ＋ 固定資産 ＋ 繰延資産
        負債及び純資産の部 ＝ 負債の部 ＋ 純資産の部
      【PL系（PL＋販売費＋製造原価 を合わせて探索）】
        売上総利益 ＝ 売上高 － 売上原価
        営業利益 ＝ 売上総利益 － 販売費及び一般管理費
        経常利益 ＝ 営業利益 ＋ 営業外収益 － 営業外費用（営業外損失）
        税引前当期利益 ＝ 経常利益 ＋ 特別利益 － 特別損失
        当期利益 ＝ 税引前当期利益 － 法人税等
    """
    Number = Union[int, float]
    PERIODS: List[str] = ["今期", "前期", "前々期"]

    # ---------- 浅いコピー ----------
    def _shallow_copy(d: Dict[str, Any]) -> Dict[str, Any]:
        copied: Dict[str, Any] = {}
        for k in ("BS", "PL", "販売費", "製造原価"):
            lst = d.get(k)
            if isinstance(lst, list):
                copied[k] = [dict(x) if isinstance(x, dict) else x for x in lst]
            elif lst is not None:
                copied[k] = lst
        # 元に存在しないキーは触らない
        for k in d.keys():
            if k not in copied:
                copied[k] = d[k]
        return copied

    # ---------- 正規化/数値化ヘルパ ----------
    def _strip_spaces(s: str) -> str:
        return re.sub(r'[\u3000\s]+', '', s) if isinstance(s, str) else s

    def _norm_label(s: Any) -> str:
        if not isinstance(s, str):
            return ""
        t = s
        t = re.sub(r'[【】\[\]（）\(\)]', '', t)
        t = _strip_spaces(t)
        t = (t.replace('及び', '・')
               .replace('および', '・')
               .replace('并び', '・')
               .replace('･', '・')
               .replace('.', '・')
               .replace('・の部', 'の部'))
        return t

    def _to_number(x: Any) -> Optional[Number]:
        if x is None:
            return None
        if isinstance(x, (int, float)):
            return x
        if isinstance(x, str):
            s = x.strip()
            if s == "":
                return None
            try:
                import unicodedata
                s = unicodedata.normalize('NFKC', s)
            except Exception:
                pass
            s = s.replace(',', '').replace('，', '')
            s = re.sub(r'[−－–—]', '-', s)
            m = re.search(r'-?\d+(?:\.\d+)?', s)
            if m:
                core = m.group(0)
                return int(core) if '.' not in core else float(core)
        return None

    def _get_raw_amount_str(rec: Dict[str, Any], period: str) -> Optional[str]:
        v = rec.get(period, {})
        raw = v.get("金額") if isinstance(v, dict) else v
        return raw if isinstance(raw, str) else None

    def _get_amount(rec: Dict[str, Any], period: str) -> Optional[Number]:
        v = rec.get(period, {})
        if isinstance(v, dict):
            return _to_number(v.get("金額"))
        return _to_number(v)

    def _set_amount(rec: Dict[str, Any], period: str, value: Number) -> None:
        if period not in rec or not isinstance(rec[period], dict):
            rec[period] = {"金額": value, "page_no": ""}
        else:
            rec[period]["金額"] = value

    def _money_to_str_no_commas(x: Number) -> str:
        s = f"{int(x) if isinstance(x, int) or (isinstance(x, float) and x.is_integer()) else x}"
        return s.replace(',', '').replace('，', '')

    def _looks_like_triangle_misread(raw: Optional[str], computed: Number) -> bool:
        if not isinstance(raw, str):
            return False
        s = raw.strip()
        try:
            import unicodedata
            s = unicodedata.normalize('NFKC', s)
        except Exception:
            pass
        s = s.replace(',', '').replace('，', '')
        s = re.sub(r'[−－–—]', '-', s)
        if s.startswith('4') or s.startswith('-4'):
            return True
        comp = _money_to_str_no_commas(computed)
        if len(s) >= 2 and s[1:] == comp:
            return True
        if s.startswith('-4') and s[2:] == comp:
            return True
        return False

    def _looks_like_triangle_misread_numeric(current: Optional[Number], computed: Number) -> bool:
        if current is None:
            return False
        try:
            a = abs(int(round(float(current))))
            b = abs(int(round(float(computed))))
        except Exception:
            return False
        if a <= b:
            return False
        if not str(a).endswith(str(b)):
            return False
        diff = a - b
        while diff % 10 == 0 and diff > 0:
            diff //= 10
        return diff == 4

    def _should_fix(raw: Optional[str], current: Optional[Number], computed: Number) -> Tuple[bool, str]:
        if current is not None and _looks_like_triangle_misread(raw, computed):
            return True, "△誤読痕跡（先頭4/-4 または先頭落とし一致）"
        if current is not None and _looks_like_triangle_misread_numeric(current, computed):
            return True, "△誤読痕跡（数値比較：先頭4落とし一致）"
        return False, ""

    # ---------- 科目パターン ----------
    pat_ryudou = [re.compile(r'^流動資産$', re.U)]
    pat_kotei = [re.compile(r'^固定資産$', re.U)]
    pat_kuriyose = [re.compile(r'^(?:繰延資産|繰越資産)$', re.U)]
    pat_shisan_total = [re.compile(r'^資産の部(?:合計|計)?$', re.U), re.compile(r'^資産合計$', re.U)]
    pat_fusai = [re.compile(r'^負債(?:の部)?$', re.U), re.compile(r'^負債合計$', re.U)]
    pat_jun = [re.compile(r'^純資産(?:の部)?$', re.U), re.compile(r'^純資産合計$', re.U)]
    pat_fusai_jun_total = [
        re.compile(r'(?:計)?負債[・･]純資産(?:の部)?(?:計|合計)?$', re.U),
        re.compile(r'(?:負債及び純資産|負債純資産)(?:の部)?(?:計|合計)?$', re.U),
        re.compile(r'^負債純資産合計$', re.U),
    ]

    pat_uriage = [re.compile(r'^売上高$', re.U), re.compile(r'^売上収益$', re.U)]
    pat_genka = [re.compile(r'^売上原価$', re.U)]
    pat_gross = [re.compile(r'^売上総利益$', re.U), re.compile(r'^粗利益$', re.U), re.compile(r'^売上総損益$', re.U)]
    pat_hanpan = [re.compile(r'^販売費及び一般管理費$', re.U), re.compile(r'^販管費$', re.U)]
    pat_eigyo = [re.compile(r'^営業利益$', re.U), re.compile(r'^営業損益$', re.U)]
    pat_eigyo_gai_shueki = [re.compile(r'^営業外収益$', re.U)]
    pat_eigyo_gai_shihi  = [re.compile(r'^営業外費用$', re.U), re.compile(r'^営業外損失$', re.U)]
    pat_keijo = [re.compile(r'^経常利益$', re.U), re.compile(r'^経常損益$', re.U)]
    pat_tokubetsu_rieki = [re.compile(r'^特別利益$', re.U)]
    pat_tokubetsu_sonshitsu = [re.compile(r'^特別損失$', re.U)]
    pat_zeinuki = [re.compile(r'^税引前当期利益$', re.U), re.compile(r'^税引前当期損益$', re.U)]
    pat_hojinzei = [re.compile(r'^(?:法人税等|法人税|法人税住民税及び事業税)$', re.U)]
    pat_touki = [re.compile(r'^当期利益$', re.U), re.compile(r'^当期損益$', re.U), re.compile(r'^当期純利益$', re.U)]

    def _find_first(items: List[Dict[str, Any]], pats: List[re.Pattern]) -> Optional[Dict[str, Any]]:
        """items から勘定科目/分類を正規化して最初に一致するものを返す（type は見ない）"""
        for it in items:
            if not isinstance(it, dict):
                continue
            name = _norm_label(it.get("勘定科目", ""))
            bunrui = _norm_label(it.get("分類", ""))
            for p in pats:
                if p.search(name) or p.search(bunrui):
                    return it
        return None

    # ---------- 本体処理 ----------
    work = data if in_place else _shallow_copy(data)
    bs_items: Optional[List[Dict[str, Any]]] = work.get("BS")

    # PL系の探索リストを作成（存在する配列を結合）
    combined_pl_items: List[Dict[str, Any]] = []
    for key in ("PL", "販売費", "製造原価"):
        lst = work.get(key)
        if isinstance(lst, list):
            combined_pl_items.extend(lst)

    # --- BS 側（既定で無効化してある式はそのまま） ---
    if isinstance(bs_items, list) and bs_items:
        rec_ryudou = _find_first(bs_items, pat_ryudou)
        rec_kotei = _find_first(bs_items, pat_kotei)
        rec_kuriyose = _find_first(bs_items, pat_kuriyose)
        rec_shisan_total = _find_first(bs_items, pat_shisan_total)
        rec_fusai = _find_first(bs_items, pat_fusai)
        rec_jun = _find_first(bs_items, pat_jun)
        rec_fusai_jun_total = _find_first(bs_items, pat_fusai_jun_total)

        if False and rec_shisan_total and (rec_ryudou or rec_kotei or rec_kuriyose):
            for period in PERIODS:
                parts = [(_get_amount(r, period) if r else 0) for r in (rec_ryudou, rec_kotei, rec_kuriyose)]
                if any(v is None for v in parts):
                    continue
                computed = sum(parts)  # type: ignore
                current = _get_amount(rec_shisan_total, period)
                raw = _get_raw_amount_str(rec_shisan_total, period)
                print(f"[BS] 期間={period} 資産合計(現状)={current} / 再計算={computed} (流動={parts[0]}, 固定={parts[1]}, 繰延={parts[2]})")
                ok, reason = _should_fix(raw, current, computed)
                if ok:
                    _set_amount(rec_shisan_total, period, computed)
                    print(f"  → 補正: 資産の部合計 {current} → {computed} （理由: {reason}）")

        if False and rec_fusai_jun_total and (rec_fusai or rec_jun):
            for period in PERIODS:
                v_f = _get_amount(rec_fusai, period) if rec_fusai else 0
                v_j = _get_amount(rec_jun, period) if rec_jun else 0
                if v_f is None or v_j is None:
                    continue
                computed = v_f + v_j  # type: ignore
                current = _get_amount(rec_fusai_jun_total, period)
                raw = _get_raw_amount_str(rec_fusai_jun_total, period)
                print(f"[BS] 期間={period} 負債純資産合計(現状)={current} / 再計算={computed} (負債={v_f}, 純資産={v_j})")
                ok, reason = _should_fix(raw, current, computed)
                if ok:
                    _set_amount(rec_fusai_jun_total, period, computed)
                    print(f"  → 補正: 負債及び純資産の部 {current} → {computed} （理由: {reason}）")

    # --- PL 系：PL＋販売費＋製造原価から検索 ---
    if combined_pl_items:
        rec_uriage   = _find_first(combined_pl_items, pat_uriage)
        rec_genka    = _find_first(combined_pl_items, pat_genka)     # ← 製造原価配列内でも拾える
        rec_gross    = _find_first(combined_pl_items, pat_gross)
        rec_hanpan   = _find_first(combined_pl_items, pat_hanpan)    # ← 販売費配列内でも拾える
        rec_eigyo    = _find_first(combined_pl_items, pat_eigyo)
        rec_eigyo_gai_shueki = _find_first(combined_pl_items, pat_eigyo_gai_shueki)
        rec_eigyo_gai_shihi  = _find_first(combined_pl_items, pat_eigyo_gai_shihi)
        rec_keijo    = _find_first(combined_pl_items, pat_keijo)
        rec_tokubetsu_rieki   = _find_first(combined_pl_items, pat_tokubetsu_rieki)
        rec_tokubetsu_sonshitsu = _find_first(combined_pl_items, pat_tokubetsu_sonshitsu)
        rec_zeinuki  = _find_first(combined_pl_items, pat_zeinuki)
        rec_hojinzei = _find_first(combined_pl_items, pat_hojinzei)
        rec_touki    = _find_first(combined_pl_items, pat_touki)

        # 売上総利益 = 売上高 - 売上原価
        if rec_gross and (rec_uriage and rec_genka):
            for period in PERIODS:
                v_u = _get_amount(rec_uriage, period)
                v_g = _get_amount(rec_genka, period)
                if v_u is None or v_g is None:
                    continue
                computed = v_u - v_g  # type: ignore
                current = _get_amount(rec_gross, period)
                raw = _get_raw_amount_str(rec_gross, period)
                print(f"[PL*] 期間={period} 売上総利益(現状)={current} / 再計算={computed} (売上高={v_u}, 売上原価={v_g})")
                ok, reason = _should_fix(raw, current, computed)
                if ok:
                    _set_amount(rec_gross, period, computed)
                    print(f"  → 補正: 売上総利益 {current} → {computed} （理由: {reason}）")

        # 営業利益 = 売上総利益 - 販売費及び一般管理費
        if rec_eigyo and (rec_gross and rec_hanpan):
            for period in PERIODS:
                v_gp  = _get_amount(rec_gross, period)
                v_sga = _get_amount(rec_hanpan, period)
                if v_gp is None or v_sga is None:
                    continue
                computed = v_gp - v_sga  # type: ignore
                current = _get_amount(rec_eigyo, period)
                raw = _get_raw_amount_str(rec_eigyo, period)
                print(f"[PL*] 期間={period} 営業利益(現状)={current} / 再計算={computed} (売上総利益={v_gp}, 販管費={v_sga})")
                ok, reason = _should_fix(raw, current, computed)
                if ok:
                    _set_amount(rec_eigyo, period, computed)
                    print(f"  → 補正: 営業利益 {current} → {computed} （理由: {reason}）")

        # 経常利益 = 営業利益 + 営業外収益 - 営業外費用
        if rec_keijo and rec_eigyo:
            for period in PERIODS:
                v_e       = _get_amount(rec_eigyo, period)
                v_out_rev = _get_amount(rec_eigyo_gai_shueki, period) if rec_eigyo_gai_shueki else 0
                v_out_cost= _get_amount(rec_eigyo_gai_shihi, period)  if rec_eigyo_gai_shihi  else 0
                if v_e is None or v_out_rev is None or v_out_cost is None:
                    continue
                computed = v_e + v_out_rev - v_out_cost  # type: ignore
                current = _get_amount(rec_keijo, period)
                raw = _get_raw_amount_str(rec_keijo, period)
                print(f"[PL*] 期間={period} 経常利益(現状)={current} / 再計算={computed} (営業利益={v_e}, 営業外収益={v_out_rev}, 営業外費用/損失={v_out_cost})")
                ok, reason = _should_fix(raw, current, computed)
                if ok:
                    _set_amount(rec_keijo, period, computed)
                    print(f"  → 補正: 経常利益 {current} → {computed} （理由: {reason}）")

        # 税引前当期利益 = 経常利益 + 特別利益 - 特別損失
        if rec_zeinuki and rec_keijo:
            for period in PERIODS:
                v_k      = _get_amount(rec_keijo, period)
                v_sp_gain= _get_amount(rec_tokubetsu_rieki, period) if rec_tokubetsu_rieki else 0
                v_sp_loss= _get_amount(rec_tokubetsu_sonshitsu, period) if rec_tokubetsu_sonshitsu else 0
                if v_k is None or v_sp_gain is None or v_sp_loss is None:
                    continue
                computed = v_k + v_sp_gain - v_sp_loss  # type: ignore
                current = _get_amount(rec_zeinuki, period)
                raw = _get_raw_amount_str(rec_zeinuki, period)
                print(f"[PL*] 期間={period} 税引前当期利益(現状)={current} / 再計算={computed} (経常={v_k}, 特別利益={v_sp_gain}, 特別損失={v_sp_loss})")
                ok, reason = _should_fix(raw, current, computed)
                if ok:
                    _set_amount(rec_zeinuki, period, computed)
                    print(f"  → 補正: 税引前当期利益 {current} → {computed} （理由: {reason}）")

        # 当期利益 = 税引前当期利益 - 法人税等
        if rec_touki and (rec_zeinuki and rec_hojinzei):
            for period in PERIODS:
                v_pre = _get_amount(rec_zeinuki, period)
                v_tax = _get_amount(rec_hojinzei, period)
                if v_pre is None or v_tax is None:
                    continue
                computed = v_pre - v_tax  # type: ignore
                current = _get_amount(rec_touki, period)
                raw = _get_raw_amount_str(rec_touki, period)
                print(f"[PL*] 期間={period} 当期利益(現状)={current} / 再計算={computed} (税引前={v_pre}, 法人税等={v_tax})")
                ok, reason = _should_fix(raw, current, computed)
                if ok:
                    _set_amount(rec_touki, period, computed)
                    print(f"  → 補正: 当期利益 {current} → {computed} （理由: {reason}）")

    return work

def change_kanjyoukamoku_07(data: Dict[str, Any], in_place: bool = True) -> Dict[str, Any]:
    """
    目的:
      1) BSの「流動資産」と「流動資産合計（同義: 流動資産計 / 流動資産の部合計）」で、
         片方のみ金額ありの期間に『金額/page_no』を片方向コピー。
      2) 追加: 「たな卸資産（棚卸資産）」と「商品」でも同様に相互補完。

    備考:
      - 0 は有効金額。None/"" は未設定。
      - 見出し（【〜】/（〜）/「〜の部」）はコピー対象から除外。
      - 行の新規作成はしない（両側レコードが存在する時のみ）。
      - in_place=False 時でも「販売費」「製造原価」配列を落とさないよう浅いコピーを拡張。
    """
    import re
    from typing import Any, Dict, List, Optional

    PERIODS = ["今期", "前期", "前々期"]

    # ------------------------------------------------------------
    # in_place=False のときは浅いコピーで原本を壊さない
    # ------------------------------------------------------------
    def _shallow_copy(d: Dict[str, Any]) -> Dict[str, Any]:
        copied: Dict[str, Any] = {}
        # ここを拡張：販売費・製造原価も保持
        for k in ("BS", "PL", "販売費", "製造原価"):
            if k in d:
                lst = d.get(k)
                if isinstance(lst, list):
                    copied[k] = [dict(x) if isinstance(x, dict) else x for x in lst]
                else:
                    copied[k] = lst
        # 上記以外のキーがあればそのまま持ち越す
        for k, v in d.items():
            if k not in copied:
                copied[k] = v
        return copied

    work = data if in_place else _shallow_copy(data)

    bs_list: Optional[List[Dict[str, Any]]] = work.get("BS")
    if not isinstance(bs_list, list) or not bs_list:
        return work

    # ------------------------------------------------------------
    # ラベル正規化（先頭番号や括弧・空白を落として照合を安定化）
    # ------------------------------------------------------------
    _LEADING_INDEX_RE = re.compile(
        r"""^
            (?:[0-9０-９]+|[IVXLCDMivxlcdm]+|[\u2160-\u217F]+)
            [\s\u3000\.\．,\，:：\-‐–—ｰ\)\]】］）\}、]*
        """,
        re.VERBOSE
    )

    def _strip_leading_index(s: str) -> str:
        if not isinstance(s, str):
            return ""
        t = s.lstrip()
        return _LEADING_INDEX_RE.sub("", t, count=1)

    def _norm_label(s: Any) -> str:
        """先頭番号無視 + 括弧/【】除去 + 空白除去 + 中黒ゆれ吸収"""
        if not isinstance(s, str):
            return ""
        t = _strip_leading_index(s)
        t = re.sub(r'[【】\[\]（）\(\)]', '', t)
        t = re.sub(r'[\u3000\s]+', '', t)
        t = t.replace('･', '・').replace('.', '・')
        return t

    # ------------------------------------------------------------
    # 金額の有無判定
    # ------------------------------------------------------------
    def _has_amount(d: Any) -> bool:
        return (
            isinstance(d, dict)
            and ("金額" in d)
            and (d.get("金額") is not None)
            and (d.get("金額") != "")
        )

    def _is_empty(d: Any) -> bool:
        return (not isinstance(d, dict)) or ("金額" not in d) or (d.get("金額") in (None, ""))

    # ------------------------------------------------------------
    # 見出し判定（【…】/（…）/「〜の部」など）
    # ------------------------------------------------------------
    _HEADING_MARKS = ('【', '】', '（', '）', '(', ')')
    _HEADING_KEYWORDS = ('の部',)

    def _is_heading_like(raw_name: str) -> bool:
        if not isinstance(raw_name, str):
            return False
        if any(ch in raw_name for ch in _HEADING_MARKS):
            return True
        if any(kw in raw_name for kw in _HEADING_KEYWORDS):
            return True
        return False

    # ------------------------------------------------------------
    # レコード探索（同名が複数ある場合は「金額あり」を優先）
    # ------------------------------------------------------------
    def _find_best_record(names: set, allow_heading: bool = False) -> Optional[Dict[str, Any]]:
        best = None
        def any_amount(rec: Dict[str, Any]) -> bool:
            return any(_has_amount(rec.get(p, {})) for p in PERIODS)
        for rec in bs_list:
            if not isinstance(rec, dict):
                continue
            raw = rec.get("勘定科目", "")
            norm = _norm_label(raw)
            if norm not in names:
                continue
            if not allow_heading and _is_heading_like(raw):
                continue
            if best is None or ((not any_amount(best)) and any_amount(rec)):
                best = rec
        return best

    # ------------------------------------------------------------
    # 双方向コピー: 片方空・片方値ありなら 値あり→空 にコピー
    # ------------------------------------------------------------
    def _ensure_period_dict(rec: Dict[str, Any], period: str) -> Dict[str, Any]:
        if period not in rec or not isinstance(rec[period], dict):
            rec[period] = {}
        return rec[period]

    def _copy_bidir(rec_a: Dict[str, Any], rec_b: Dict[str, Any]) -> None:
        for p in PERIODS:
            a = rec_a.get(p, {})
            b = rec_b.get(p, {})
            if _is_empty(a) and _has_amount(b):
                dst = _ensure_period_dict(rec_a, p)
                dst["金額"] = b.get("金額")
                dst["page_no"] = b.get("page_no")
            elif _is_empty(b) and _has_amount(a):
                dst = _ensure_period_dict(rec_b, p)
                dst["金額"] = a.get("金額")
                dst["page_no"] = a.get("page_no")

    # ========== (1) 流動資産 ↔ 流動資産合計（同義語含む） ==========
    rec_ryudou = _find_best_record({"流動資産"})
    rec_ryudou_total = _find_best_record({"流動資産合計", "流動資産計", "流動資産の部合計"})
    if rec_ryudou is not None and rec_ryudou_total is not None:
        _copy_bidir(rec_ryudou, rec_ryudou_total)

    # ========== (2) たな卸資産（棚卸資産） ↔ 商品 ==========
    INVENTORY_NAMES = {"たな卸資産", "棚卸資産"}
    rec_inventory = _find_best_record(INVENTORY_NAMES)
    rec_shouhin = _find_best_record({"商品"})
    if rec_inventory is not None and rec_shouhin is not None:
        _copy_bidir(rec_inventory, rec_shouhin)

    return work


from typing import Any, Dict, List, Optional, Union

def change_kanjyoukamoku_08(km_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    入出力ともに kanjyokamoku の配列を扱う軽量クリーナー。
    - 勘定科目名中の「任意の括弧で囲まれた『合計』」を括弧ごと削除。
    - 変換後の名称が既に他レコードに存在し、かつ今期/前期/前々期の
      '双方が数値を持つ期間すべて'で金額が一致する場合、
      元の「(合計)付き」レコードを削除する。

    使い方:
      xxxxx["kanjyokamoku"] = change_kanjyoukamoku_08(xxxxx["kanjyokamoku"])
    """
    Number = Union[int, float]  # ← 追加（未定義だったため）

    # 任意の開き/閉じ括弧（半角/全角、多種）
    OPEN_BRACKETS  = r'\(\（\[\［\{\｛<＜〈《「『【｟〔'
    CLOSE_BRACKETS = r'\)\）\]\］\}\｝>＞〉》」』】｠〕'
    BRACKETED_GOKEI_RE = re.compile(rf'[{OPEN_BRACKETS}]\s*合計\s*[{CLOSE_BRACKETS}]')

    PERIODS = ("今期", "前期", "前々期")

    def _norm_name(s: str) -> str:
        # 空白（全角/半角）を無視した比較用
        return re.sub(r'[\u3000\s]+', '', s) if isinstance(s, str) else ""

    def _to_number(x: Any) -> Optional[Number]:
        if x is None:
            return None
        if isinstance(x, (int, float)):
            return x
        if isinstance(x, str):
            s = x.strip()
            if s == "":
                return None
            try:
                import unicodedata
                s = unicodedata.normalize('NFKC', s)
            except Exception:
                pass
            s = s.replace(',', '').replace('，', '')
            s = s.replace('△', '-').replace('▲', '-')
            s = re.sub(r'[−－–—]', '-', s)
            if re.fullmatch(r'-?\d+(\.\d+)?', s):
                return int(s) if '.' not in s else float(s)
        if isinstance(x, dict):
            return _to_number(x.get("金額"))
        return None

    def _get_amount(rec: Dict[str, Any], period: str) -> Optional[Number]:
        v = rec.get(period, {})
        return _to_number(v)

    # まず浅いコピー
    items = [dict(it) if isinstance(it, dict) else it for it in (km_list or [])]

    # 正規化後の名前（空白無視）→ インデックス集合
    def _index_by_norm_name(lst: List[Dict[str, Any]]):
        idx_map: Dict[str, List[int]] = {}
        for i, it in enumerate(lst):
            if not isinstance(it, dict):
                continue
            nm = _norm_name(it.get("勘定科目", ""))
            idx_map.setdefault(nm, []).append(i)
        return idx_map

    # 実処理：変換・比較・削除判定
    to_delete = set()
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        name = it.get("勘定科目", "")
        if not isinstance(name, str):
            continue

        # 「(合計)」を括弧ごと削除
        cleaned = BRACKETED_GOKEI_RE.sub('', name)
        cleaned = re.sub(r'[ \t\u3000]+', ' ', cleaned).strip()

        # 変化がなければスキップ
        if cleaned == name:
            continue

        norm_cleaned = _norm_name(cleaned)
        idx_map = _index_by_norm_name(items)
        candidate_idxs = [j for j in idx_map.get(norm_cleaned, []) if j != i and j not in to_delete]

        if not candidate_idxs:
            # 同名が無いなら置換だけ実施
            items[i]["勘定科目"] = cleaned
            continue

        # 金額比較：双方が数値を持つ期間だけ比較対象。全部一致なら削除。
        def amounts_match(j: int) -> bool:
            all_common_equal = True
            has_any_common = False
            for p in PERIODS:
                a = _get_amount(items[i], p)
                b = _get_amount(items[j], p)
                if a is not None and b is not None:
                    has_any_common = True
                    if a != b:
                        all_common_equal = False
                        break
            return has_any_common and all_common_equal

        matched_existing = next((j for j in candidate_idxs if amounts_match(j)), None)
        if matched_existing is not None:
            to_delete.add(i)
        else:
            # 金額が一致しない場合は名称を保持（衝突回避のため）
            pass

    # 削除を反映
    result: List[Dict[str, Any]] = []
    for i, it in enumerate(items):
        if i in to_delete:
            continue
        result.append(it)

    return result
def change_kanjyoukamoku_09(data: Dict[str, Any], in_place: bool = True) -> Dict[str, Any]:
    """
    目的:
      - 各配列（BS/PL/販売費/製造原価）内で、勘定科目が
          「XXXX」 と 「XXXX（2）/XXXX(2)」
        のペアを見つけ、
        '双方が数値を持つ期間すべて' で金額が一致する場合に
        「（2）」側のレコードを削除する。

    I/O:
      - { "BS": [...], "PL": [...], "販売費": [...], "製造原価": [...] } を受け取り、同形式で返す。
    """
    PERIODS: List[str] = ["今期", "前期", "前々期"]

    # ---------- 浅いコピー ----------
    def _shallow_copy(d: Dict[str, Any]) -> Dict[str, Any]:
        copied: Dict[str, Any] = {}
        for k in ("BS", "PL", "販売費", "製造原価"):
            lst = d.get(k)
            if isinstance(lst, list):
                copied[k] = [dict(x) if isinstance(x, dict) else x for x in lst]
            else:
                copied[k] = lst
        for k, v in d.items():
            if k not in copied:
                copied[k] = v
        return copied

    work = data if in_place else _shallow_copy(data)

    # ---------- 正規化/数値化ヘルパ ----------
    def _strip_spaces(s: str) -> str:
        return re.sub(r'[\u3000\s]+', '', s) if isinstance(s, str) else s

    def _norm_label(s: Any) -> str:
        if not isinstance(s, str):
            return ""
        t = s
        t = re.sub(r'[【】\[\]（）\(\)]', '', t)
        t = _strip_spaces(t)
        t = (t.replace('及び', '・')
               .replace('および', '・')
               .replace('并び', '・')
               .replace('･', '・')
               .replace('.', '・')
               .replace('・の部', 'の部'))
        return t

    # 末尾の「(2) / （2）」を外して“素名称”を作る
    _TRAILING_TWO_RE = re.compile(r'(?:[ \u3000]*[（(]\s*2\s*[)）])$')

    def _strip_trailing_two(s: str) -> str:
        if not isinstance(s, str):
            return ""
        core = s.strip()
        core = _TRAILING_TWO_RE.sub('', core)
        core = re.sub(r'[ \t\u3000]+', ' ', core).strip()
        return core

    Number = Union[int, float]

    def _to_number(x: Any) -> Optional[Number]:
        if x is None:
            return None
        if isinstance(x, (int, float)):
            return x
        if isinstance(x, str):
            s = x.strip()
            if s == "":
                return None
            try:
                import unicodedata
                s = unicodedata.normalize('NFKC', s)
            except Exception:
                pass
            s = s.replace(',', '').replace('，', '')
            s = re.sub(r'[−－–—]', '-', s)
            if re.fullmatch(r'-?\d+(\.\d+)?', s):
                return int(s) if '.' not in s else float(s)
        if isinstance(x, dict):
            return _to_number(x.get("金額"))
        return None

    def _get_amount(rec: Dict[str, Any], period: str) -> Optional[Number]:
        v = rec.get(period, {})
        return _to_number(v)

    # '双方が数値を持つ期間すべて' で金額が一致するか
    def _amounts_match(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
        all_common_equal = True
        has_any_common = False
        for p in PERIODS:
            va = _get_amount(a, p)
            vb = _get_amount(b, p)
            if va is not None and vb is not None:
                has_any_common = True
                if va != vb:
                    all_common_equal = False
                    break
        return has_any_common and all_common_equal

    # ---------- 本体処理 ----------
    def _process_one_list(items: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
        if not isinstance(items, list) or not items:
            return items

        # インデックス収集：
        #  - base_map: 素名称（末尾(2)除去＆正規化）→ base候補(素) のインデックス集合
        #  - dup_map : 素名称 → (2)付き のインデックス集合
        base_map: Dict[str, List[int]] = {}
        dup_map: Dict[str, List[int]] = {}

        for i, it in enumerate(items):
            if not isinstance(it, dict):
                continue
            raw = it.get("勘定科目", "")
            if not isinstance(raw, str):
                continue

            norm = _norm_label(raw)
            # (2) を除いた“素名称”の正規化
            norm_base = _norm_label(_strip_trailing_two(raw))

            # 「(2) 付きか？」は、正規化前の raw 末尾で判定（空白許容）
            if _TRAILING_TWO_RE.search(raw):
                dup_map.setdefault(norm_base, []).append(i)
            else:
                base_map.setdefault(norm_base, []).append(i)

        to_delete: set[int] = set()

        # 素名称ごとに、base と dup のペアで比較し、マッチする dup を削除
        for norm_base, dup_idxs in dup_map.items():
            base_idxs = base_map.get(norm_base, [])
            if not base_idxs:
                continue
            for di in dup_idxs:
                if di in to_delete:
                    continue
                drec = items[di]
                # どれか一つでも一致すれば (2) を消して良い
                matched = False
                for bi in base_idxs:
                    if bi in to_delete:
                        continue
                    brec = items[bi]
                    if _amounts_match(brec, drec):
                        matched = True
                        break
                if matched:
                    # ログ表示（削除する具体名）
                    print(f"  → 削除: 『{items[di].get('勘定科目', '')}』 "
                          f"(理由: 素名称『{_strip_trailing_two(items[di].get('勘定科目', '') or '')}』の同額レコードが存在)")
                    to_delete.add(di)

        if not to_delete:
            return items

        new_items: List[Dict[str, Any]] = []
        for i, it in enumerate(items):
            if i in to_delete:
                continue
            new_items.append(it)
        return new_items

    # 各トップキーに適用
    for key in ("BS", "PL", "販売費", "製造原価"):
        if key in work:
            work[key] = _process_one_list(work.get(key))

    return work

def change_kanjyoukamoku_10(all_results: List[Dict[str, Any]], in_place: bool = True) -> List[Dict[str, Any]]:
    """
    目的:
      - ページ単位（各 page_data["kanjyokamoku"] 内）で、
        『XXXX』と『XXXX（2）/XXXX(2)』『XXXX（3）/XXXX(3)』の重複を検知し、
        双方が数値を持つ期間で金額がすべて一致する場合、番号付き（2/3）側を削除する。
      - 追加仕様:
        page_data["type"] が ["販売費"] の場合、
        そのページ内の全ての勘定科目に対して
          * item["type"] = "販売費"
          * item["分類"]   = "販売費及び一般管理費"
        を設定する。

    入力:
      all_results: List[Dict]  # 例: [{"kanjyokamoku": [ {...}, {...} ]}, ...]

    返り値:
      in_place=True  : 参照を破壊的に更新して返す
      in_place=False : 浅いコピーを作って返す
    """
    PERIODS = ("今期", "前期", "前々期")

    # ---------- in_place=False の浅いコピー ----------
    if not in_place:
        copied: List[Dict[str, Any]] = []
        for page_data in all_results:
            if isinstance(page_data, dict):
                new_page = dict(page_data)
                km_list = page_data.get("kanjyokamoku", [])
                if isinstance(km_list, list):
                    new_page["kanjyokamoku"] = [
                        dict(item) if isinstance(item, dict) else item for item in km_list
                    ]
                copied.append(new_page)
            else:
                copied.append(page_data)
        work = copied
    else:
        work = all_results

    # ---------- ヘルパ ----------
    def _strip_spaces(s: str) -> str:
        """全角/半角スペースをすべて除去。"""
        return re.sub(r'[\u3000\s]+', '', s) if isinstance(s, str) else s

    # 末尾の「(2)/(３)/（2）/（３）」を検出（空白許容）
    _TRAILING_N_RE = re.compile(r'(?:[ \u3000]*[（(]\s*([23２３])\s*[)）])$')

    def _strip_trailing_n(s: str) -> str:
        """末尾の（2）/（3）などを削除し、比較用の素名称を返す（空白統一）。"""
        if not isinstance(s, str):
            return ""
        core = _TRAILING_N_RE.sub('', s.strip())
        core = re.sub(r'[ \t\u3000]+', ' ', core).strip()
        return core

    Number = Union[int, float]

    def _to_number(x: Any) -> Optional[Number]:
        """数値化（'', None は None。全角/カンマ/△▲/全角マイナス等を吸収）。"""
        if x is None:
            return None
        if isinstance(x, (int, float)):
            return x
        if isinstance(x, str):
            s = x.strip()
            if s == "":
                return None
            try:
                import unicodedata
                s = unicodedata.normalize('NFKC', s)
            except Exception:
                pass
            s = s.replace(',', '').replace('，', '')
            s = s.replace('△', '-').replace('▲', '-')
            s = re.sub(r'[−－–—]', '-', s)
            if re.fullmatch(r'-?\d+(\.\d+)?', s):
                return int(s) if '.' not in s else float(s)
            return None
        if isinstance(x, dict):
            return _to_number(x.get("金額"))
        return None

    def _get_amount(rec: Dict[str, Any], period: str) -> Optional[Number]:
        v = rec.get(period, {})
        return _to_number(v)

    def _amounts_match(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
        """
        双方が数値を持つ期間すべてで一致、かつ少なくとも1期間は双方数値。
        """
        all_equal = True
        any_common = False
        for p in PERIODS:
            va = _get_amount(a, p)
            vb = _get_amount(b, p)
            if va is not None and vb is not None:
                any_common = True
                if va != vb:
                    all_equal = False
                    break
        return any_common and all_equal

    # ---------- 本体処理（ページごと） ----------
    for page_data in work:
        if not isinstance(page_data, dict):
            continue
        km_list = page_data.get("kanjyokamoku", [])
        if not isinstance(km_list, list) or not km_list:
            continue

        # ★ 追加仕様：page_data["type"] が ["販売費"] の場合、全項目に type/分類 を付与・更新
        page_type = page_data.get("type")
        if (isinstance(page_type, list) and "販売費" in page_type) or page_type == ["販売費"]:
            for item in km_list:
                if isinstance(item, dict):
                    item["type"] = "販売費"
                    item["分類"] = "販売費及び一般管理費"

        # インデックス収集：
        #  - base_map: 素名称（末尾(2)/(3)除去＆空白正規化）→ base候補のインデックス集合
        #  - dup_map : 素名称 → (2)/(3)付きのインデックス集合
        base_map: Dict[str, List[int]] = {}
        dup_map: Dict[str, List[int]] = {}

        for i, it in enumerate(km_list):
            if not isinstance(it, dict):
                continue
            raw = it.get("勘定科目", "")
            if not isinstance(raw, str):
                continue

            has_suffix = _TRAILING_N_RE.search(raw)
            base = _strip_spaces(_strip_trailing_n(raw))

            if has_suffix:
                dup_map.setdefault(base, []).append(i)
            else:
                base_map.setdefault(base, []).append(i)

        to_delete: set[int] = set()

        # 素名称ごとに、(2)/(3)側が base のどれかと一致（数値）すれば削除
        for base, dup_idxs in dup_map.items():
            base_idxs = base_map.get(base, [])
            if not base_idxs:
                continue
            for di in dup_idxs:
                if di in to_delete:
                    continue
                drec = km_list[di]
                # どれか1つでも一致したら OK
                matched = any(
                    _amounts_match(km_list[bi], drec) for bi in base_idxs if bi not in to_delete
                )
                if matched:
                    # ログ（任意）
                    nm = drec.get("勘定科目", "")
                    base_pretty = _TRAILING_N_RE.sub('', nm or '').strip()
                    print(f"  → 削除: 『{nm}』 (理由: 素名称『{base_pretty}』と金額一致)")
                    to_delete.add(di)

        if to_delete:
            page_data["kanjyokamoku"] = [
                it for i, it in enumerate(km_list) if i not in to_delete
            ]

    return work


def change_kanjyoukamoku_11(all_results: List[Dict[str, Any]], in_place: bool = True) -> List[Dict[str, Any]]:
    """
    目的:
      - all_results を先頭から走査し、各 page_data において
        page_data["type"] リスト内に「販売費」が *含まれていない* 場合、
        その page_data["kanjyokamoku"] 配下の全要素のうち
        item["type"] が「販売費」のものを「PL」に変更する。

    入力:
      all_results: List[Dict]  # 例: [{"kanjyokamoku": [ {...}, ... ], "type": ["BS or PL", ...]}, ...]
      in_place: bool           # True: 破壊的変更 / False: 浅いコピーを作成して返す

    返り値:
      List[Dict]  # all_results と同形。in_place=False の場合は浅いコピー。
    """

    # ---------- in_place=False の浅いコピー ----------
    if not in_place:
        copied: List[Dict[str, Any]] = []
        for page_data in all_results:
            if isinstance(page_data, dict):
                new_page = dict(page_data)
                km_list = page_data.get("kanjyokamoku", [])
                if isinstance(km_list, list):
                    new_page["kanjyokamoku"] = [
                        dict(item) if isinstance(item, dict) else item for item in km_list
                    ]
                copied.append(new_page)
            else:
                # None など非辞書要素はそのまま
                copied.append(page_data)
        work = copied
    else:
        work = all_results

    # ---------- 本体処理 ----------
    for page_data in work:
        if not isinstance(page_data, dict):
            continue

        km_list = page_data.get("kanjyokamoku", [])
        if not isinstance(km_list, list) or not km_list:
            continue

        page_type = page_data.get("type")
        # 「販売費」が type リストに含まれているページは対象外
        has_hanbaihi = isinstance(page_type, list) and ("販売費" in page_type)
        if has_hanbaihi:
            continue

        # 対象ページ: item.type=="販売費" → "PL" に変更
        for item in km_list:
            if isinstance(item, dict) and item.get("type") == "販売費":
                item["type"] = "PL"

    return work
from typing import List, Dict, Any

def change_kanjyoukamoku_12(all_results: List[Dict[str, Any]], in_place: bool = True) -> List[Dict[str, Any]]:
    """
    目的:
      - 各ページ(page_data)について、page_data["kanjyokamoku"] 配下を先頭から走査し、
        条件に合致する項目( type=="製造原価" かつ 勘定科目名に「合」「計」を両方含む )を
        発見順にリネームする。
          1件目 -> idx<8 のとき「期首材料費合計」、idx>=12 のとき「経費合計」（8<=idx<=11 は変更しない）
          2件目 -> 「経費合計」
        (3件目以降は変更しない)

    入出力:
      - フォーマットは change_kanjyoukamoku_11 と同一。
      - in_place=False の場合は浅いコピーを作成して返す。
    """

    # ---------- in_place=False の浅いコピー ----------
    if not in_place:
        copied: List[Dict[str, Any]] = []
        for page_data in all_results:
            if isinstance(page_data, dict):
                new_page = dict(page_data)
                km_list = page_data.get("kanjyokamoku", [])
                if isinstance(km_list, list):
                    new_page["kanjyokamoku"] = [
                        dict(item) if isinstance(item, dict) else item for item in km_list
                    ]
                copied.append(new_page)
            else:
                copied.append(page_data)
        work = copied
    else:
        work = all_results

    NAME_KEYS = [
        "name", "科目", "勘定科目", "kamoku", "kanjyoukamoku",
        "kanjyoukamokumei", "kanjyo_kamoku", "account", "account_name"
    ]

    # ---------- 本体処理 ----------
    for page_data in work:
        if not isinstance(page_data, dict):
            continue

        km_list = page_data.get("kanjyokamoku", [])
        if not isinstance(km_list, list) or not km_list:
            continue

        # マッチ項目のインデックス収集 (ページ単位)
        match_indices: List[int] = []
        for idx, item in enumerate(km_list):
            if not isinstance(item, dict):
                continue
            if item.get("type") != "製造原価":
                continue

            name_key = next((k for k in NAME_KEYS if k in item and isinstance(item.get(k), str)), None)
            if not name_key:
                continue

            name_val = item.get(name_key, "")
            if not isinstance(name_val, str):
                continue

            if ("合" in name_val) and ("計" in name_val):
                match_indices.append(idx)

        # 発見順 1件目
        if len(match_indices) >= 1:
            idx0 = match_indices[0]
            item0 = km_list[idx0]
            name_key0 = next((k for k in NAME_KEYS if k in item0 and isinstance(item0.get(k), str)), None)
            if name_key0:
                if idx0 < 8:
                    item0[name_key0] = "期首材料費合計"
                elif idx0 >= 12:
                    item0[name_key0] = "経費合計"
                # 8 <= idx0 <= 11 の場合は変更しない

        # 発見順 2件目
        if len(match_indices) >= 2:
            idx1 = match_indices[1]
            item1 = km_list[idx1]
            name_key1 = next((k for k in NAME_KEYS if k in item1 and isinstance(item1.get(k), str)), None)
            if name_key1:
                item1[name_key1] = "経費合計"

    return work

def change_kanjyoukamoku_13(rows: List[Dict[str, Any]], in_place: bool = True) -> List[Dict[str, Any]]:
    """
    目的:
      - フラットな行配列 rows を先頭から走査し、
        0) ★新規: すべての勘定科目名の末尾にある最後の「△」を削除
        1) 勘定科目名が「当期製品製造原価」で始まり、完全一致ではないものを「当期製品製造原価」に正規化
        2) 条件 (type=="製造原価" かつ 勘定科目名に「合」「計」を両方含む) を満たす行を
           発見順に以下へリネーム:
             1件目 -> 「期首材料費合計」
                     ※ただし、その item が rows の「先頭から 8 項目以内（0始まりで index < 8）」にある場合のみセット
                     ※追加仕様: その item が「12番目以後（0始まりで index >= 12）」の場合は「経費合計」に変更
             2件目 -> 「経費合計」
           3件目以降は変更しない。
      - ★追加: 勘定科目名が「仕入戻し高」の場合、「仕入割戻し高」に変更（全行対象）

    入出力:
      - rows: 例の形式のリスト[dict]（各要素は1行）
      - 返り値: rows と同形。in_place=False の場合は浅いコピーを返す。

    注意:
      - 勘定科目名キーは環境差を考慮し、候補キーから最初に見つかったものを使用。
    """

    work = rows if in_place else [dict(r) if isinstance(r, dict) else r for r in rows]

    NAME_KEYS = [
        "勘定科目", "科目", "name", "kamoku",
        "account", "account_name", "kanjyoukamoku",
        "kanjyoukamokumei", "kanjyo_kamoku"
    ]

    hit_count = 0

    for idx, item in enumerate(work):
        if not isinstance(item, dict):
            continue

        # 勘定科目名のキー特定
        name_key = next((k for k in NAME_KEYS if k in item and isinstance(item.get(k), str)), None)
        if not name_key:
            continue

        name_val = item.get(name_key, "")
        if not isinstance(name_val, str):
            continue

        # 0) ★新規: 末尾の「△」を1つだけ除去（後続の処理はこの正規化後の値で進める）
        # 末尾の空白 + 「△」 + 空白 をまとめて落とすが、△は1個だけに限定
        trimmed_once = re.sub(r'\s*△\s*$', '', name_val)
        if trimmed_once != name_val:
            item[name_key] = trimmed_once
            name_val = trimmed_once

        # ★新規: 「仕入戻し高」→「仕入割戻し高」（全行対象）
        if name_val.strip() == "仕入戻し高":
            item[name_key] = "仕入割戻し高"
            name_val = item[name_key]
            print("[rename] 仕入戻し高 -> 仕入割戻し高")

        # ここから下は type == 「製造原価」だけに適用
        if item.get("type") != "製造原価":
            continue

        # 1) 「当期製品製造原価xxxxxxxx」を「当期製品製造原価」に正規化
        if name_val.startswith("当期製品製造原価") and name_val.strip() != "当期製品製造原価":
            item[name_key] = "当期製品製造原価"
            name_val = item[name_key]

        # 2) 「合」と「計」を両方含むか（合計系のリネーム）
        if ("合" in name_val) and ("計" in name_val):
            hit_count += 1
            if hit_count == 1:
                if idx < 8:
                    item[name_key] = "期首材料費合計"
                elif idx >= 12:
                    item[name_key] = "経費合計"
                # 8〜11 は変更しない
            elif hit_count == 2:
                item[name_key] = "経費合計"
            else:
                pass

    return work
def change_kanjyoukamoku_14(data: Dict[str, Any], in_place: bool = True) -> Dict[str, Any]:
    """
    目的:
      - data["BS"] のみ対象にし、「流動資産」となる項目について、
        今期/前期/前々期の金額が “すべて” 空文字("") または 0（ゼロ）である場合、
        その項目を丸ごと削除する。

    I/O:
      - 入力・出力は change_kanjyoukamoku_09 と同じ構造
        { "BS": [...], "PL": [...], "販売費": [...], "製造原価": [...] } を受け取り、同形式で返す。
    """
    PERIODS: List[str] = ["今期", "前期", "前々期"]

    # ---------- 浅いコピー（in_place=False のときのみ） ----------
    def _shallow_copy(d: Dict[str, Any]) -> Dict[str, Any]:
        copied: Dict[str, Any] = {}
        for k in ("BS", "PL", "販売費", "製造原価"):
            lst = d.get(k)
            if isinstance(lst, list):
                copied[k] = [dict(x) if isinstance(x, dict) else x for x in lst]
            else:
                copied[k] = lst
        # それ以外のキーはそのまま
        for k, v in d.items():
            if k not in copied:
                copied[k] = v
        return copied

    work = data if in_place else _shallow_copy(data)

    # ---------- ラベル正規化 ----------
    def _strip_spaces(s: str) -> str:
        # 全角スペース含む空白全削除
        return re.sub(r'[\u3000\s]+', '', s) if isinstance(s, str) else s

    def _norm_label(s: Any) -> str:
        """括弧や記号のゆらぎを吸収して比較用のラベルにする。"""
        if not isinstance(s, str):
            return ""
        t = s
        # かっこ類の除去
        t = re.sub(r'[【】\[\]（）\(\)]', '', t)
        # 空白の除去
        t = _strip_spaces(t)
        # よくある区切りの正規化（必要最小限）
        t = (t.replace('及び', '・')
               .replace('および', '・')
               .replace('并び', '・')
               .replace('･', '・')
               .replace('.', '・')
               .replace('・の部', 'の部'))
        return t

    TARGET_LABEL = _norm_label("流動資産")

    Number = Union[int, float]

    def _to_number(x: Any) -> Optional[Number]:
        """数値（int/float）へ。空文字は None、'0' は 0、dict は {"金額": ...} を参照。"""
        if x is None:
            return None
        if isinstance(x, (int, float)):
            return x
        if isinstance(x, str):
            s = x.strip()
            if s == "":
                return None  # 空は None 扱い
            # 全角→半角などの正規化
            try:
                import unicodedata
                s = unicodedata.normalize('NFKC', s)
            except Exception:
                pass
            # カンマ、全角カンマ除去／ダッシュ類をハイフンに
            s = s.replace(',', '').replace('，', '')
            s = re.sub(r'[−－–—]', '-', s)
            if re.fullmatch(r'-?\d+(\.\d+)?', s):
                return int(s) if '.' not in s else float(s)
            return None
        if isinstance(x, dict):
            # {"金額": ...} 想定
            return _to_number(x.get("金額"))
        return None

    def _get_amount(rec: Dict[str, Any], period: str) -> Optional[Number]:
        """rec[period] または rec[period]['金額'] から数値へ。空は None。"""
        v = rec.get(period, {})
        return _to_number(v)

    def _all_three_empty_or_zero(rec: Dict[str, Any]) -> bool:
        """
        今期/前期/前々期の全てが 空("") -> None 扱い または 0 のとき True。
        （None も“空”として可。）
        """
        vals: List[Optional[Number]] = [_get_amount(rec, p) for p in PERIODS]
        # いずれも None(=空) または 0
        return all((v is None) or (v == 0) for v in vals)

    # ---------- 本体（BSのみ処理） ----------
    bs_items = work.get("BS")
    if isinstance(bs_items, list) and bs_items:
        to_delete: set[int] = set()
        for idx, rec in enumerate(bs_items):
            if not isinstance(rec, dict):
                continue
            # 勘定科目の同定
            raw_label = rec.get("勘定科目", "")
            if _norm_label(raw_label) != TARGET_LABEL:
                continue

            # 3期間すべて空 or 0 なら削除対象
            if _all_three_empty_or_zero(rec):
                # ログ出力（必要なければコメントアウト可）
                print(f"  → 削除: 『{raw_label}』 (今期/前期/前々期が空または0)")
                to_delete.add(idx)

        if to_delete:
            work["BS"] = [rec for i, rec in enumerate(bs_items) if i not in to_delete]

    return work
def change_kanjyoukamoku_15(data: Dict[str, Any], in_place: bool = True) -> Dict[str, Any]:
    """
    目的:
      - 各配列（BS/PL/販売費/製造原価）内で、同じ勘定科目（必要に応じて分類も同一）の
        レコードを1つにマージする。
      - 各期間（今期/前期/前々期）について「金額」と「page_no」を統合する。
        * 片方しか金額が無ければある方を採用（page_no も）
        * 両方に金額があり等しければ page_no を補完（空なら相手のを入れる）
        * 両方に金額があり不一致なら **後に来るレコードを優先（後勝ちで上書き）**

    I/O:
      - { "BS": [...], "PL": [...], "販売費": [...], "製造原価": [...] } を受け取り、同形式で返す。
    """
    PERIODS: List[str] = ["今期", "前期", "前々期"]
    Number = Union[int, float]

    # ---------- 浅いコピー ----------
    def _shallow_copy(d: Dict[str, Any]) -> Dict[str, Any]:
        copied: Dict[str, Any] = {}
        for k in ("BS", "PL", "販売費", "製造原価"):
            lst = d.get(k)
            if isinstance(lst, list):
                copied[k] = [dict(x) if isinstance(x, dict) else x for x in lst]
            else:
                copied[k] = lst
        for k, v in d.items():
            if k not in copied:
                copied[k] = v
        return copied

    work = data if in_place else _shallow_copy(data)

    # ---------- 正規化/数値化ヘルパ ----------
    def _strip_spaces(s: str) -> str:
        return re.sub(r'[\u3000\s]+', '', s) if isinstance(s, str) else s

    def _norm_label(s: Any) -> str:
        if not isinstance(s, str):
            return ""
        t = s
        t = re.sub(r'[【】\[\]（）\(\)]', '', t)  # 括弧類の除去
        t = _strip_spaces(t)
        t = (t.replace('及び', '・')
               .replace('および', '・')
               .replace('并び', '・')
               .replace('･', '・')
               .replace('.', '・')
               .replace('・の部', 'の部'))
        return t

    def _to_number(x: Any) -> Optional[Number]:
        if x is None:
            return None
        if isinstance(x, (int, float)):
            return x
        if isinstance(x, str):
            s = x.strip()
            if s == "":
                return None
            try:
                import unicodedata
                s = unicodedata.normalize('NFKC', s)
            except Exception:
                pass
            s = s.replace(',', '').replace('，', '')
            s = re.sub(r'[−－–—]', '-', s)
            if re.fullmatch(r'-?\d+(\.\d+)?', s):
                return int(s) if '.' not in s else float(s)
        if isinstance(x, dict):
            return _to_number(x.get("金額"))
        return None

    def _get_period_dict(rec: Dict[str, Any], period: str) -> Dict[str, Any]:
        """ rec[period] を辞書 {"金額":..., "page_no":...} 形式で返す（欠損時は空辞書）。 """
        v = rec.get(period)
        if isinstance(v, dict):
            return {"金額": v.get("金額", ""), "page_no": v.get("page_no", "")}
        elif v is None:
            return {"金額": "", "page_no": ""}
        else:
            # 数値や文字列が直入れされている場合にも対応
            return {"金額": v, "page_no": ""}

    def _get_amount(rec: Dict[str, Any], period: str) -> Optional[Number]:
        return _to_number(_get_period_dict(rec, period).get("金額"))

    def _get_page_no(rec: Dict[str, Any], period: str) -> Any:
        return _get_period_dict(rec, period).get("page_no", "")

    def _ensure_period_dict(rec: Dict[str, Any], period: str) -> None:
        """ rec[period] を辞書形式に正規化しておく """
        pd = _get_period_dict(rec, period)
        rec[period] = {"金額": pd.get("金額", ""), "page_no": pd.get("page_no", "")}

    # acc(これまでの集約) と cur(後に来たレコード) をマージ
    # 金額不一致時は「後勝ち」で acc を cur の値で上書きする。
    def _merge_record_later_wins(acc: Dict[str, Any], cur: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(acc)  # シャローコピー
        for p in PERIODS:
            _ensure_period_dict(merged, p)
            va = _to_number(merged[p].get("金額"))
            pa = merged[p].get("page_no", "")
            vb = _get_amount(cur, p)
            pb = _get_page_no(cur, p)

            if va is None and vb is None:
                # どちらも金額なし → 変更なし
                continue
            if va is None and vb is not None:
                # 相手にだけ金額 → 採用
                merged[p]["金額"] = vb
                merged[p]["page_no"] = pb
                continue
            if va is not None and vb is None:
                # こちらにだけ金額 → 既存維持
                continue

            # 両方に金額あり
            if va == vb:
                # 等しければ page_no 補完（こちら空かつ相手非空）
                if (pa in (None, "",)) and (pb not in (None, "",)):
                    merged[p]["page_no"] = pb
            else:
                # 不一致 → 後勝ちで金額を上書き。page_no は後者が非空なら置換、空なら保持
                merged[p]["金額"] = vb
                if pb not in (None, "",):
                    merged[p]["page_no"] = pb
                # pb が空なら既存 pa を維持

        # メタ情報（勘定科目/分類 など）は基本 acc を優先、空なら cur を補完
        for meta_k in ("勘定科目", "分類", "type", "no"):
            if meta_k in merged or meta_k in cur:
                if not merged.get(meta_k) and cur.get(meta_k):
                    merged[meta_k] = cur.get(meta_k)

        return merged

    # ---------- 本体処理 ----------
    def _process_one_list(items: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
        if not isinstance(items, list) or not items:
            return items

        # 同一グループ化キー: (勘定科目の正規化, 分類の正規化 or "")
        def _group_key(rec: Dict[str, Any]) -> tuple:
            name = _norm_label(rec.get("勘定科目", ""))
            cls  = _norm_label(rec.get("分類", "")) if rec.get("分類") else ""
            return (name, cls) if name else (name, "")

        groups: Dict[tuple, List[Dict[str, Any]]] = {}
        order: List[tuple] = []

        for it in items:
            if not isinstance(it, dict):
                # 非辞書はそのまま別グループ扱い
                k = ("__RAW__", id(it))
                groups.setdefault(k, []).append(it)
                order.append(k)
                continue

            k = _group_key(it)
            if k not in groups:
                order.append(k)
            groups.setdefault(k, []).append(it)

        result: List[Dict[str, Any]] = []

        for k in order:
            recs = groups.get(k, [])
            if not recs:
                continue

            if k[0] == "__RAW__":
                result.extend(recs)
                continue

            # 後勝ちルールなので、出現順に畳み込み
            acc: Optional[Dict[str, Any]] = None
            for rec in recs:
                if acc is None:
                    acc = dict(rec)
                    for p in PERIODS:
                        _ensure_period_dict(acc, p)
                else:
                    acc = _merge_record_later_wins(acc, rec)

            if acc is not None:
                result.append(acc)

        return result

    # 各トップキーに適用
    for key in ("BS", "PL", "販売費", "製造原価"):
        if key in work:
            work[key] = _process_one_list(work.get(key))

    return work



def change_kanjyoukamoku_16(
    one_type: Optional[str],
    rows: List[Dict[str, Any]],
    in_place: bool = True
) -> List[Dict[str, Any]]:
    """
    目的:
      - one_type に応じて rows 内の各行の type を再設定する。

        one_type が:
          - "BS or PL" の場合:
              type が "販売費" の項目の type を "PL" に変更する
          - "販売費" の場合:
              すべての項目の type を "販売費" に変更する
          - "製造原価" の場合:
              すべての項目の type を "製造原価" に変更する
              さらに、以下の追加ロジックを適用する:
                - 最後の item["勘定科目"] が「○○○○○合計」（語尾が「合計」）で、
                  かつ rows の中に「販売費及び一般管理費」が存在しない場合、
                  最後の item["勘定科目"] を「販売費及び一般管理費」に変更する
          - 上記以外または None の場合:
              何もしない（rows をそのまま返す）

    入出力:
      - rows: 行(dict)のリスト
      - 返り値: rows と同形。in_place=False の場合は浅いコピーを返す。
    """

    # in_place=False の場合は shallow copy
    work = rows if in_place else [
        dict(r) if isinstance(r, dict) else r
        for r in rows
    ]

    if one_type not in ("BS or PL", "販売費", "製造原価"):
        # 想定外 or None の場合は何もしない
        return work

    # --- type の付け替え ---
    for item in work:
        if not isinstance(item, Dict):
            continue

        current_type = item.get("type")

        if one_type == "BS or PL":
            # 「BS or PL」ページの場合：
            #   type が「販売費」のものだけ「PL」に変える
            if current_type == "販売費":
                item["type"] = "PL"

        elif one_type == "販売費":
            # 「販売費」ページの場合：
            #   全ての行の type を「販売費」にする
            item["type"] = "販売費"

        elif one_type == "製造原価":
            # 「製造原価」ページの場合：
            #   全ての行の type を「製造原価」にする
            item["type"] = "製造原価"

    # --- 追加ロジック（製造原価ページ専用） ---
    if one_type == "販売費":
        # work 内に「販売費及び一般管理費」が存在するかチェック
        has_sga = any(
            isinstance(it, Dict) and it.get("勘定科目") == "販売費及び一般管理費"
            for it in work
        )

        if not has_sga:
            # 最後の dict 行を探す
            last_idx = None
            for idx in range(len(work) - 1, -1, -1):
                if isinstance(work[idx], Dict):
                    last_idx = idx
                    break

            if last_idx is not None:
                last_item = work[last_idx]
                kamoku = last_item.get("勘定科目")

                # 「○○○○○合計」＝ 語尾が「合計」の文字列と解釈
                if isinstance(kamoku, str) and kamoku.endswith("合計"):
                    last_item["勘定科目"] = "販売費及び一般管理費"

    return work



def change_kanjyoukamoku_17(data: Dict[str, Any], in_place: bool = False) -> Dict[str, Any]:
    """
    補正ロジック（完全版・2025/01 修正版）

    - △、▲、Δ、▵、▴、∧、Λ などの誤OCR → 4 と誤読されてマイナスにすべき値が
      4xxxx と読まれたケースの補正（従来機能）

    - 売上総利益が括弧（ ）によりマイナスと誤認した場合の補正（今回追加）
      条件：
          ・対象期の売上総利益がマイナス
          ・対象期の売上高（純売上高／売上高／売上高合計など）が存在
          ・対象期の売上原価が存在
          ・abs(売上総利益) == 売上高 - 売上原価
      これを満たす場合、売上総利益を正の差額へ補正する
      （今期・前期・前々期すべて対象）
    """

    if not in_place:
        data = copy.deepcopy(data)

    BS = data.get("BS", [])
    PL = data.get("PL", [])

    # ---- PL の「売上高」候補一覧 ----
    SALES_LABELS = {
        "売上高",
        "純売上高",
        "売上高合計",
        "売上（合計）",
        "純売上",
        "売上",
    }

    PERIOD_KEYS = ("今期", "前期", "前々期")

    def _find_record(list_, labels):
        for rec in list_:
            if _norm_label(rec.get("勘定科目", "")) in labels:
                return rec
        return None

    def _get_amount(rec, period_key: str):
        if not rec:
            return None
        period_obj = rec.get(period_key) or {}
        val = period_obj.get("金額")
        return val if isinstance(val, (int, float)) else None

    # 売上総利益系
    GROSS_PROFIT_LABELS = {"売上総利益", "売上総損失"}

    rec_sales = _find_record(PL, SALES_LABELS)
    rec_cost  = _find_record(PL, {"売上原価"})
    rec_gross = _find_record(PL, GROSS_PROFIT_LABELS)

    # ------------------------------
    # ★ 従来の △／4 誤読補正 判定ロジック
    # ------------------------------
    def _is_4_misread(original: int, computed: int) -> bool:
        """
        既存の 4/△ 誤OCR の補正判断ロジック（元コードを踏襲）
        """
        if original < 0:
            return False

        s1 = str(original)
        s2 = str(computed)

        # a) 文字数差 1 以内で、1〜2桁目が 4 に見えているケース
        if abs(len(s1) - len(s2)) <= 1 and s1[0] == "4":
            return True

        # b) 差が 10 の倍数になり最終的に 4 で終わる
        d = abs(original - computed)
        while d >= 10 and d % 10 == 0:
            d //= 10
        return d == 4

    # ------------------------------
    # ★ 新規追加：売上総利益の符号補正（今期・前期・前々期すべて）
    # ------------------------------
    if rec_gross and rec_sales and rec_cost:
        for period in PERIOD_KEYS:
            gp_now     = _get_amount(rec_gross, period)
            sales_now  = _get_amount(rec_sales, period)
            cost_now   = _get_amount(rec_cost, period)

            # 売上総利益がマイナス & 売上高/売上原価が数値として揃っている場合のみ対象
            if (
                isinstance(gp_now, (int, float)) and gp_now < 0 and
                isinstance(sales_now, (int, float)) and
                isinstance(cost_now, (int, float))
            ):
                computed = sales_now - cost_now

                # 判定：売上総利益が誤ってマイナスになっている
                if abs(gp_now) == computed:
                    print(f"[補正] 売上総利益の誤符号補正({period}): {gp_now} → {computed}")
                    rec_gross[period]["金額"] = computed

    # ------------------------------
    # ★ 既存の △／4 誤読補正 も各期に適用
    # ------------------------------
    if rec_gross and rec_sales and rec_cost:
        for period in PERIOD_KEYS:
            gp_now = _get_amount(rec_gross, period)
            s_now  = _get_amount(rec_sales, period)
            c_now  = _get_amount(rec_cost, period)

            if (
                isinstance(gp_now, (int, float)) and
                isinstance(s_now, (int, float)) and
                isinstance(c_now, (int, float))
            ):
                computed = s_now - c_now

                if _is_4_misread(gp_now, computed):
                    print(f"[補正] △/4誤読補正({period}): {gp_now} → {computed}")
                    rec_gross[period]["金額"] = computed

    return data
def change_kanjyoukamoku_18(data: Dict[str, Any],in_place: bool = False,margelist_path: str = "margelist",) -> Dict[str, Any]:
    """
    勘定科目マージロジック（change_kanjyoukamoku_18）

    対象:
        data["BS"], data["PL"], data["販売費"], data["製造原価"] それぞれのリスト

    処理:
        - margelist ファイル（カンマ区切り）に定義された勘定科目グループごとに、
          同一グループ内の勘定科目を「同一勘定科目」とみなしてマージする
        - 同じリスト内で先に現れたレコードを基準とし、後ろにあるレコードを基準にマージ
        - 先に出るレコードの勘定科目・金額・page_no を優先
          （ただし、基準側が空で後続側が数値を持つ場合は補完する）
        - マージ後は後続側のレコードをリストから削除し、基準レコードのみ残す

    戻り値:
        - in_place=True の場合は引数 data 自体を書き換え、その参照を返す
        - in_place=False の場合は deepcopy した新しい dict を返す
    """

    if not in_place:
        data = copy.deepcopy(data)

    # margelist からマージ対象グループを読み込み（行数無制限）
    raw_groups = _load_merge_groups(margelist_path=margelist_path)
    print("raw_groups::::")
    print(margelist_path)
    print("[DEBUG] raw_groups from margelist:", raw_groups)
    # 正規化済みラベルのセットも併せ持つ形にしておく
    # groups_norm: List[Tuple[List[str], Set[str]]]
    groups_norm: List[Tuple[List[str], set]] = []
    for labels in raw_groups:
        norm_set = {_norm_label(lbl) for lbl in labels}
        groups_norm.append((labels, norm_set))

    PERIOD_KEYS = ("今期", "前期", "前々期")

    def _merge_section(section: List[Dict[str, Any]], section_name: str) -> None:
        """
        1つのセクション（BS / PL / 販売費 / 製造原価）について
        margelist を使って勘定科目をマージする。
        """
        if not isinstance(section, list) or not section:
            return

        # 削除対象インデックスを記録して、最後に一括削除する
        to_delete_indices: set = set()

        # 各マージグループごとに処理
        for labels, norm_set in groups_norm:
            # このセクション内で、当該グループに該当するレコードのインデックスを収集
            matched_indices: List[int] = []
            for idx, rec in enumerate(section):
                if idx in to_delete_indices:
                    # すでに他グループでマージ元として吸収された（削除予定）ならスキップ
                    continue
                name = rec.get("勘定科目", "")
                if _norm_label(name) in norm_set:
                    matched_indices.append(idx)

            # 1件以下ならマージ不要
            if len(matched_indices) <= 1:
                continue

            # 先に出るレコードを基準レコードとする
            base_idx = matched_indices[0]
            base_rec = section[base_idx]
            base_name = base_rec.get("勘定科目", "")

            for idx in matched_indices[1:]:
                if idx in to_delete_indices:
                    continue

                merge_rec = section[idx]
                merge_name = merge_rec.get("勘定科目", "")

                # 各期ごとに金額・page_no をマージ
                for period_key in PERIOD_KEYS:
                    base_period = base_rec.get(period_key)
                    merge_period = merge_rec.get(period_key)

                    # どちらもキー自体が無い場合 or 両方 None/空 dict の場合はスキップ
                    if not base_period and not merge_period:
                        continue

                    # dict を保証（存在しない場合は空 dict として扱う）
                    if base_period is None:
                        base_period = {}
                        base_rec[period_key] = base_period
                    if merge_period is None:
                        merge_period = {}

                    base_amount = base_period.get("金額")
                    merge_amount = merge_period.get("金額")

                    # 「数値を持っているか」の判定
                    def has_num(v):
                        return isinstance(v, (int, float))

                    # 金額のマージ
                    if not has_num(base_amount) and has_num(merge_amount):
                        # 基準側が数値を持っていない場合のみ、後続から補完
                        base_period["金額"] = merge_amount

                    # page_no のマージ（page_no も同様に「先に出るもの優先」だが、空なら補完）
                    base_page = base_period.get("page_no")
                    merge_page = merge_period.get("page_no")
                    if (base_page in (None, "", 0)) and (merge_page not in (None, "")):
                        base_period["page_no"] = merge_page

                # このレコードは基準に吸収したので削除予定にマーク
                to_delete_indices.add(idx)

                # ログ（不要なら print 行は削除してOK）
                print(f"[マージ] {section_name}: '{merge_name}' → '{base_name}'")

        # 実際に削除を反映（インデックスの昇順で filter）
        if to_delete_indices:
            section[:] = [
                rec for i, rec in enumerate(section) if i not in to_delete_indices
            ]

    # 各セクションに対してマージ実行
    for key in ("PL", "BS", "販売費", "製造原価"):
        section = data.get(key)
        if isinstance(section, list):
            _merge_section(section, section_name=key)

    return data

def change_kanjyoukamoku_19(data: Dict[str, Any], in_place: bool = False) -> Dict[str, Any]:
    """
    BS 並び替えロジック（change_kanjyoukamoku_17 と同じ入出力仕様）

    data["BS"] 例：
    [
        {
            "勘定科目": "流動資産",
            "分類": "流動資産",
            "今期": {...},
            "前期": {...},
            "前々期": {...}
        },
        {
            "勘定科目": "現金及び預金",
            "分類": "流動資産",
            ...
        },
        ...
        {
            "勘定科目": "負債・純資産合計",
            "分類": "負債純資産合計",
            ...
        }
    ]

    処理内容：
      1) data["BS"] の中で、分類 == 「資産合計」の行が
         「末尾から 10 件以内」に出現していることを条件とする。
         → 該当しない場合は何もしないでそのまま返す。

      2) 「最初に出現する『流動負債』行」を探す。
         ここでは勘定科目 or 分類 が「流動負債」の行を先頭とみなす。
         → 見つからなければ何もしない。

      3) その「最初の流動負債行」の “後ろにある” 行のうち、
            勘定科目が
              - 「資産〜」で始まる もしくは
              - 「〜資産」で終わる
         行をすべて抜き出し、
         それらを元の相対順序を保ったまま
         「最初の流動負債行の直前」に移動する。

         ※ 移動した行どうしの順番は崩さない
         ※ 移動しなかった行どうしの順番も崩さない
         ※ 各レコード内部の値（今期・前期・page_no など）は一切変更しない
    """

    if not in_place:
        data = copy.deepcopy(data)

    BS: List[Dict[str, Any]] = data.get("BS") or []
    if not isinstance(BS, list) or not BS:
        return data

    n = len(BS)

    # -------------------------------------------------
    # 1. 「分類 == 資産合計」が末尾 10 件以内にあるか？
    # -------------------------------------------------
    asset_total_near_tail = False
    for idx, rec in enumerate(BS):
        if rec.get("分類") == "資産合計":
            # 残り要素数が 10 以下なら「末尾から 10 件以内」
            if n - idx <= 10:
                asset_total_near_tail = True
                break

    if not asset_total_near_tail:
        # 条件を満たさない場合は並び替えを行わない
        return data

    # -------------------------------------------------
    # 2. 最初の「流動負債」行（見出し）を探す
    #    勘定科目 or 分類 が「流動負債」のもの
    # -------------------------------------------------
    first_ryuudou_fusai_idx = None
    for idx, rec in enumerate(BS):
        if rec.get("勘定科目") == "流動負債" or rec.get("分類") == "流動負債":
            first_ryuudou_fusai_idx = idx
            break

    if first_ryuudou_fusai_idx is None:
        # 「流動負債」ブロックが無い場合は何もしない
        return data

    # -------------------------------------------------
    # 3. 流動負債先頭行の「後ろ」にある
    #    「資産〜」or「〜資産」の勘定科目を持つ行を前方へ移動
    # -------------------------------------------------
    before = BS[:first_ryuudou_fusai_idx]               # 流動負債より前の全行
    head_liab = BS[first_ryuudou_fusai_idx]             # 最初の流動負債行
    tail = BS[first_ryuudou_fusai_idx + 1 :]            # その後ろの行たち

    moved_assets: List[Dict[str, Any]] = []
    tail_remain: List[Dict[str, Any]] = []

    for rec in tail:
        # 「種類」は存在しないので、「勘定科目」で判定する
        account = str(rec.get("勘定科目") or "")
        if account.startswith("資産") or account.endswith("資産"):
            moved_assets.append(rec)
        else:
            tail_remain.append(rec)

    # 移動対象がなければ何もしない
    if not moved_assets:
        return data

    # 新しい並びを構成
    #   [もともと流動負債より前の行]
    #   + [移動対象の資産系行（相対順序維持）]
    #   + [最初の流動負債行]
    #   + [残りの行（相対順序維持）]
    new_BS = before + moved_assets + [head_liab] + tail_remain
    data["BS"] = new_BS

    # デバッグログが必要ならコメントアウトを外してください
    # print("[change_kanjyoukamoku_19] moved:", [r.get("勘定科目") for r in moved_assets])

    return data




def change_kanjyoukamoku_20(data: Dict[str, Any], in_place: bool = False) -> Dict[str, Any]:
    """
    PL の「当期純利益」「親会社株主に帰属する当期純利益」
    「非支配株主持分に帰属する当期純利益」の金額が取り違えられている
    可能性を補正する。

    【入出力仕様】
    - change_kanjyoukamoku_19 と同じく、引数 data は
      {
        "PL": [
          {"勘定科目": "...", "分類": "...", "今期": {...}, "前期": {...}, "前々期": {...}},
          ...
        ],
        "BS": [...],
        ...
      }
      のような dict を想定する。
    - 戻り値も同じ data 形式。in_place=False の場合は deep copy を返す。

    【処理概要】
      1) data["PL"] から以下の3科目を探す（部分一致・日本語ベース）：
         - total_rec : 「当期純利益」（親会社・非支配の文言を含まないもの）
         - parent_rec: 「親会社株主に帰属する当期純利益」
         - nc_rec    : 「非支配株主持分に帰属する当期純利益」

      2) parent_rec と nc_rec の今期金額を比較し、
         「非支配株主持分に帰属する当期純利益 ＞ 親会社株主に帰属する当期純利益」
         の場合は、両者の今期金額を入れ替える。

      3) 前期・前々期についても、両方数値が入っていて
         非支配 > 親会社 となっている場合は同様に入れ替える。

    ※ 「当期純利益」の金額自体は変更しない。
       役割は「内訳チェックとログ用」であり、ここでは参照のみとする。
    """
    Number = Union[int, float]
    def _to_number(value: Any) -> Optional[Number]:
        """
        各レコードの 金額 フィールドを安全に数値化するためのヘルパー。
        数値に変換できない場合は None を返す。
        """
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            s = value.strip()
            if not s:
                return None
            try:
                # カンマ区切りも一応許容（"11,415" → 11415）
                return int(s.replace(",", ""))
            except ValueError:
                try:
                    return float(s.replace(",", ""))
                except ValueError:
                    return None
        return None
    
    if not in_place:
        data = copy.deepcopy(data)

    PL: List[Dict[str, Any]] = data.get("PL") or []
    if not isinstance(PL, list) or not PL:
        return data

    total_idx: Optional[int] = None
    parent_idx: Optional[int] = None
    nc_idx: Optional[int] = None

    # ------------------------------
    # 1. 対象3科目のレコードを特定
    # ------------------------------
    for idx, rec in enumerate(PL):
        name = str(rec.get("勘定科目") or "")

        # 親会社株主に帰属する当期純利益
        if ("親会社" in name or "親会社株主" in name) and ("当期" in name and "純利益" in name):
            # なるべく厳密に "親会社株主に帰属する当期純利益" を優先
            if "親会社株主に帰属する当期純利益" in name or "親会社株主に帰属する当期利益" in name:
                parent_idx = idx
                continue
            # 上記以外でも親会社＋当期＋純利益を含むなら親会社扱い
            if parent_idx is None:
                parent_idx = idx
                continue

        # 非支配株主持分に帰属する当期純利益
        if ("非支配" in name or "非支配株主持分" in name) and ("当期" in name and "純利益" in name):
            if "非支配株主持分に帰属する当期純利益" in name or "非支配株主持分に帰属する当期利益" in name:
                nc_idx = idx
                continue
            if nc_idx is None:
                nc_idx = idx
                continue

        # 全体の当期純利益（親会社・非支配を含まない）
        if ("当期" in name and "純利益" in name
                and "親会社" not in name
                and "非支配" not in name):
            # 例：「当期純利益」「当期（四半期）純利益」などを想定
            if total_idx is None:
                total_idx = idx

    # 親会社と非支配のどちらかが見つからない場合は何もしない
    if parent_idx is None or nc_idx is None:
        return data

    parent_rec = PL[parent_idx]
    nc_rec = PL[nc_idx]
    total_rec = PL[total_idx] if total_idx is not None else None

    # ------------------------------
    # 2. 各期ごとに金額の大小をみて補正
    # ------------------------------
    def maybe_swap(period_key: str) -> None:
        """
        period_key: "今期" / "前期" / "前々期"
        親会社と非支配の金額を比較し、非支配 > 親会社 のときに入れ替える。
        """

        p_period = parent_rec.get(period_key) or {}
        n_period = nc_rec.get(period_key) or {}

        p_val = _to_number((p_period or {}).get("金額"))
        n_val = _to_number((n_period or {}).get("金額"))

        # 両方とも数値でない（None）の場合は何もしない
        if p_val is None or n_val is None:
            return

        # 非支配の方が大きい場合のみ入れ替える
        if n_val > p_val:
            parent_rec[period_key]["金額"], nc_rec[period_key]["金額"] = (
                n_val,
                p_val,
            )

    for key in ("今期", "前期", "前々期"):
        maybe_swap(key)

    # ここでは total_rec は参照のみ（必要なら検算ログなどに利用可能）
    # total_rec は変更しない前提。

    return data

def change_kanjyoukamoku_21(data: Dict[str, Any], in_place: bool = False) -> Dict[str, Any]:
    """
    隣接する2レコードについて、
    勘定科目名が IGNORE_TOKENS（「費」「先」「品」「の部」「部」）だけ違う
    かつ ユーザー指定の金額条件を満たす場合に
    両者を1レコードに合弁する。

    合弁条件（あなたの指定どおり）:
      (今期1 == 今期2 or 今期1 == "" or 今期2 == "") AND
      (前期1 == 前期2 or 前期1 == "" or 前期2 == "") AND
      (前々期1 == 前々期2 or 前々期1 == "" or 前々期2 == "")

    合弁後の名前は「文字数が長い方」を採用し、
    金額は空の方へもう一方の非空金額をコピーする。

    対象配列: PL / BS / 販売費 / 製造原価
    """

    Number = Union[int, float]

    IGNORE_TOKENS: List[str] = ["の部", "費", "先", "品", "部", "・","高","当期","用","リー"]

    #----------------------------------------
    # 金額関連ユーティリティ
    #----------------------------------------
    def _get_raw(rec: Dict[str, Any], period: str) -> str:
        p = rec.get(period)
        if not isinstance(p, dict):
            return ""
        v = p.get("金額")
        if v is None:
            return ""
        s = str(v).strip()
        return s

    def _is_empty(s: str) -> bool:
        return s == ""

    def _to_number(val: Any) -> Optional[Union[int, float]]:
        if isinstance(val, (int, float)):
            return val
        if isinstance(val, str):
            s = val.replace(",", "").strip()
            if not s:
                return None
            try:
                return int(s)
            except:
                try:
                    return float(s)
                except:
                    return None
        return None

    def _amount_equal_or_empty(a: str, b: str) -> bool:
        # 条件そのもの: (a==b) or (a=="" or b=="")
        if a == b:
            return True
        if _is_empty(a) or _is_empty(b):
            return True

        # フォーマット違い対策として数値比較も許可
        na = _to_number(a)
        nb = _to_number(b)
        if na is not None and nb is not None and na == nb:
            return True

        return False

    def _can_merge_amounts(rec1: Dict[str, Any], rec2: Dict[str, Any]) -> bool:
        for period in ("今期", "前期", "前々期"):
            a = _get_raw(rec1, period)
            b = _get_raw(rec2, period)
            if not _amount_equal_or_empty(a, b):
                return False
        return True

    #----------------------------------------
    # 名前の差分チェック
    #----------------------------------------
    def _normalize(name: str) -> str:
        n = name
        for token in IGNORE_TOKENS:
            n = n.replace(token, "")
        return n

    #----------------------------------------
    # 2レコード合弁
    #----------------------------------------
    def _merge_2(rec1: Dict[str, Any], rec2: Dict[str, Any]) -> Dict[str, Any]:
        # 長い方の名前を採用
        name1 = str(rec1.get("勘定科目") or "")
        name2 = str(rec2.get("勘定科目") or "")
        if len(name2) > len(name1):
            base = copy.deepcopy(rec2)
            other = rec1
        else:
            base = copy.deepcopy(rec1)
            other = rec2

        # 金額をコピー
        for period in ("今期", "前期", "前々期"):
            base_val = _get_raw(base, period)
            if not _is_empty(base_val):
                continue

            other_val = _get_raw(other, period)
            if not _is_empty(other_val):
                if not isinstance(base.get(period), dict):
                    base[period] = {}
                base[period]["金額"] = other_val

        return base

    #----------------------------------------
    # 本体処理
    #----------------------------------------
    if not in_place:
        data = copy.deepcopy(data)

    target_keys = ["PL", "BS", "販売費", "製造原価"]

    for key in target_keys:
        arr = data.get(key)
        if not isinstance(arr, list) or not arr:
            continue

        new_arr: List[Dict[str, Any]] = []
        i = 0
        n = len(arr)

        while i < n:
            rec1 = arr[i]
            if i == n - 1:
                new_arr.append(rec1)
                break

            rec2 = arr[i + 1]

            # dict 以外はそのまま
            if not isinstance(rec1, dict) or not isinstance(rec2, dict):
                new_arr.append(rec1)
                i += 1
                continue

            name1 = str(rec1.get("勘定科目") or "")
            name2 = str(rec2.get("勘定科目") or "")

            # 名前のベース一致（IGNORE_TOKENS 除外後）
            if _normalize(name1) == _normalize(name2) and name1 != name2:
                # 金額条件をチェック
                if _can_merge_amounts(rec1, rec2):
                    merged = _merge_2(rec1, rec2)
                    new_arr.append(merged)
                    i += 2
                    continue

            # 合弁しない場合
            new_arr.append(rec1)
            i += 1

        data[key] = new_arr

    return data

def change_kanjyoukamoku_22(data: Dict[str, Any], in_place: bool = False) -> Dict[str, Any]:
    """
    勘定科目が「資産の部」または「負債の部」であり、
    今期・前期・前々期の「金額」がすべて 0 または "" の場合に
    そのレコードを削除する。

    対象配列: PL / BS / 販売費 / 製造原価
    入出力は change_kanjyoukamoku_21 と同じ。
    """

    if not in_place:
        data = copy.deepcopy(data)

    #----------------------------------------
    # 金額関連ユーティリティ
    #----------------------------------------
    def _get_raw_amount(rec: Dict[str, Any], period: str) -> str:
        """
        rec[period]["金額"] を文字列で取得。
        存在しない場合や None の場合は "" を返す。
        """
        p = rec.get(period)
        if not isinstance(p, dict):
            return ""
        v = p.get("金額")
        if v is None:
            return ""
        return str(v).strip()

    def _to_number(val: Any) -> Optional[Union[int, float]]:
        if isinstance(val, (int, float)):
            return val
        if isinstance(val, str):
            s = val.replace(",", "").strip()
            if not s:
                return None
            try:
                return int(s)
            except:
                try:
                    return float(s)
                except:
                    return None
        return None

    def _is_zero_or_empty(s: str) -> bool:
        """
        "" または 数値として 0 と解釈できるものを True とする。
        """
        if s == "":
            return True
        num = _to_number(s)
        if num is not None and num == 0:
            return True
        return False

    def _all_periods_zero_or_empty(rec: Dict[str, Any]) -> bool:
        """
        今期・前期・前々期の金額がすべて 0 または "" なら True。
        """
        for period in ("今期", "前期", "前々期"):
            s = _get_raw_amount(rec, period)
            if not _is_zero_or_empty(s):
                return False
        return True

    #----------------------------------------
    # 本体処理
    #----------------------------------------
    target_keys = ["PL", "BS", "販売費", "製造原価"]

    for key in target_keys:
        arr = data.get(key)
        if not isinstance(arr, list) or not arr:
            continue

        new_arr: List[Dict[str, Any]] = []

        for rec in arr:
            # dict 以外はそのまま残す
            if not isinstance(rec, dict):
                new_arr.append(rec)
                continue

            name = str(rec.get("勘定科目") or "")

            # 勘定科目が「資産の部」または「負債の部」で、
            # 3期すべて 0 or "" のときは削除（= new_arr に追加しない）
            if name in ("資産の部", "負債の部") and _all_periods_zero_or_empty(rec):
                # スキップ＝削除
                continue

            # 上記条件に該当しないものは残す
            new_arr.append(rec)

        data[key] = new_arr

    return data

def change_kanjyoukamoku_23(data: Dict[str, Any], in_place: bool = False) -> Dict[str, Any]:
    """
    特定の語句グループ（例: 「収益」「収入」「利益」）だけが違う勘定科目名を
    同一とみなして、隣接2レコードを合弁する処理。

    - 「事業収益」「事業収入」「事業利益」などをマージ対象にする。
    - 合弁条件（change_kanjyoukamoku_21 と同じ金額条件）:
        (今期1 == 今期2 or 今期1 == "" or 今期2 == "") AND
        (前期1 == 前期2 or 前期1 == "" or 前期2 == "") AND
        (前々期1 == 前々期2 or 前々期1 == "" or 前々期2 == "")
    - 合弁後の名前は「文字数が長い方」を採用し、
      金額は空の方へもう一方の非空金額をコピーする。

    MERGE_WORD_GROUPS にパターンを追加することで簡単に拡張できる。
    対象配列: PL / BS / 販売費 / 製造原価
    """

    Number = Union[int, float]

    # ここを編集するだけで新しいパターンを追加できる
    MERGE_WORD_GROUPS: List[List[str]] = [
        # グループ0: 収益/収入/利益
        ["収益", "収入", "利益"],

        # 例: 将来追加したくなったらここに追記
        # ["売上高", "売上"],
        # ["営業収益", "営業収入"],
    ]

    #----------------------------------------
    # 金額関連ユーティリティ（21からほぼコピー）
    #----------------------------------------
    def _get_raw(rec: Dict[str, Any], period: str) -> str:
        p = rec.get(period)
        if not isinstance(p, dict):
            return ""
        v = p.get("金額")
        if v is None:
            return ""
        s = str(v).strip()
        return s

    def _is_empty(s: str) -> bool:
        return s == ""

    def _to_number(val: Any) -> Optional[Union[int, float]]:
        if isinstance(val, (int, float)):
            return val
        if isinstance(val, str):
            s = val.replace(",", "").strip()
            if not s:
                return None
            try:
                return int(s)
            except:
                try:
                    return float(s)
                except:
                    return None
        return None

    def _amount_equal_or_empty(a: str, b: str) -> bool:
        # 条件そのもの: (a==b) or (a=="" or b=="")
        if a == b:
            return True
        if _is_empty(a) or _is_empty(b):
            return True

        # フォーマット違い対策として数値比較も許可
        na = _to_number(a)
        nb = _to_number(b)
        if na is not None and nb is not None and na == nb:
            return True

        return False

    def _can_merge_amounts(rec1: Dict[str, Any], rec2: Dict[str, Any]) -> bool:
        for period in ("今期", "前期", "前々期"):
            a = _get_raw(rec1, period)
            b = _get_raw(rec2, period)
            if not _amount_equal_or_empty(a, b):
                return False
        return True

    #----------------------------------------
    # 名前の差分チェック（語句グループ単位で正規化）
    #----------------------------------------
    def _normalize_by_groups(name: str) -> str:
        """
        MERGE_WORD_GROUPS の各グループについて、
        グループ内の語を同一のプレースホルダに置き換える。

        例:
          MERGE_WORD_GROUPS[0] == ["収益", "収入", "利益"] の場合
          "事業収益" -> "事業__G0__"
          "事業収入" -> "事業__G0__"
          "事業利益" -> "事業__G0__"
        """
        n = name
        for idx, group in enumerate(MERGE_WORD_GROUPS):
            placeholder = f"__G{idx}__"
            for token in group:
                if token in n:
                    n = n.replace(token, placeholder)
        return n

    #----------------------------------------
    # 2レコード合弁（21と同じロジック）
    #----------------------------------------
    def _merge_2(rec1: Dict[str, Any], rec2: Dict[str, Any]) -> Dict[str, Any]:
        # 長い方の名前を採用
        name1 = str(rec1.get("勘定科目") or "")
        name2 = str(rec2.get("勘定科目") or "")
        if len(name2) > len(name1):
            base = copy.deepcopy(rec2)
            other = rec1
        else:
            base = copy.deepcopy(rec1)
            other = rec2

        # 金額をコピー
        for period in ("今期", "前期", "前々期"):
            base_val = _get_raw(base, period)
            if not _is_empty(base_val):
                continue

            other_val = _get_raw(other, period)
            if not _is_empty(other_val):
                if not isinstance(base.get(period), dict):
                    base[period] = {}
                base[period]["金額"] = other_val

        return base

    #----------------------------------------
    # 本体処理
    #----------------------------------------
    if not in_place:
        data = copy.deepcopy(data)

    target_keys = ["PL", "BS", "販売費", "製造原価"]

    for key in target_keys:
        arr = data.get(key)
        if not isinstance(arr, list) or not arr:
            continue

        new_arr: List[Dict[str, Any]] = []
        i = 0
        n = len(arr)

        while i < n:
            rec1 = arr[i]
            if i == n - 1:
                new_arr.append(rec1)
                break

            rec2 = arr[i + 1]

            # dict 以外はそのまま
            if not isinstance(rec1, dict) or not isinstance(rec2, dict):
                new_arr.append(rec1)
                i += 1
                continue

            name1 = str(rec1.get("勘定科目") or "")
            name2 = str(rec2.get("勘定科目") or "")

            norm1 = _normalize_by_groups(name1)
            norm2 = _normalize_by_groups(name2)

            # グループ正規化後の名前が同じ & 元の名前は違う場合のみ対象
            if norm1 == norm2 and name1 != name2:
                # 金額条件をチェック
                if _can_merge_amounts(rec1, rec2):
                    merged = _merge_2(rec1, rec2)
                    new_arr.append(merged)
                    i += 2
                    continue

            # 合弁しない場合
            new_arr.append(rec1)
            i += 1

        data[key] = new_arr

    return data
