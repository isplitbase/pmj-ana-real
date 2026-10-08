# -*- coding: utf-8 -*-
"""勘定科目マスタの読み込み(GCS)と、科目名 → 候補(candidate)の照合

マスタは test1 の cron(/data/pmj_cron/master_to_gcs.php)が GCS に送っている
v2ac_kanjo_master.json を読む。
  pmj-ana      … gs://pmjbase/v2ac_kanjo_master.json
  pmj-ana-real … gs://pmjbase/real/v2ac_kanjo_master.json
(環境変数 MASTER_GCS_URI で変更可)。更新されていれば読み直す(確認は MASTER_CHECK_SEC 秒ごと)。

照合は v2ac の account_DB(v2ac_account.py)をそのまま使い、エンジンと同じ順で試す:
  1. 範囲内で完全一致(search_variety_codes)
  2. 範囲内で類似(search_variety_title ratio 0.7) → コード化(search_variety_code)
  3. 文字の含まれ方で拾う(add_candidate_syou)
  4. 範囲内で類似(ratio 0.2)
  範囲で見つからなければ、広い範囲(BS 全体 / PL 全体)でもう一度。
"""
import difflib
import json
import os
import re
import threading
import time
import unicodedata

from pmjana.v2ac_account import account_DB, add_candidate_syou, remove_brackets

MASTER_GCS_URI = os.environ.get("MASTER_GCS_URI", "gs://pmjbase/real/v2ac_kanjo_master.json")
MASTER_CHECK_SEC = int(os.environ.get("MASTER_CHECK_SEC", "600"))

_lock = threading.Lock()
_state = {"db": None, "generation": None, "checked_at": 0.0, "uri": MASTER_GCS_URI, "count": 0, "updated": None}


def _parse_gs(uri):
    if not uri.startswith("gs://"):
        raise ValueError("MASTER_GCS_URI は gs:// で始めてください: " + uri)
    bucket, _, name = uri[5:].partition("/")
    return bucket, name


def get_db():
    """account_DB を返す(GCS のマスタが更新されていれば作り直す)"""
    with _lock:
        now = time.time()
        if _state["db"] is not None and now - _state["checked_at"] < MASTER_CHECK_SEC:
            return _state["db"]
        from google.cloud import storage
        bucket, name = _parse_gs(MASTER_GCS_URI)
        blob = storage.Client().bucket(bucket).get_blob(name)
        if blob is None:
            if _state["db"] is not None:
                _state["checked_at"] = now
                return _state["db"]
            raise RuntimeError("勘定科目マスタが GCS にありません: " + MASTER_GCS_URI)
        if _state["db"] is None or blob.generation != _state["generation"]:
            master_json = json.loads(blob.download_as_bytes().decode("utf-8"))
            _state["db"] = account_DB(master_json=master_json,
                                      revise_subject_path=os.path.join(os.path.dirname(__file__), "v2ac_subject.csv"))
            _state["generation"] = blob.generation
            _state["count"] = len(master_json.get("kanjo_master", []))
            _state["updated"] = blob.updated.isoformat() if blob.updated else None
        _state["checked_at"] = now
        return _state["db"]


def master_info():
    return {"uri": _state["uri"], "generation": _state["generation"], "count": _state["count"], "updated": _state["updated"]}


# 行頭の番号・記号(v2ac の preprocess_chars と同じ考え方。括弧は照合関数側で外す)
_HEAD_NUM = re.compile(r"^(?:[\(（][0-9０-９一二三四五六七八九十]+[\)）]|[0-9０-９]+[\.．、]|[①-⑳㉑-㉟㊱-㊿]|[ア-ン][\.．、])")
_SPACES = re.compile(r"[\s　]+")
_HANKANA = re.compile(r"[｡-ﾟ]+")


def normalize_label(label):
    s = str(label or "")
    # 半角カナだけ全角にする(マスタは全角。括弧などはマスタに全角のまま入っているので変えない)
    s = _HANKANA.sub(lambda m: unicodedata.normalize("NFKC", m.group(0)), s)
    s = _SPACES.sub("", s)
    s = _HEAD_NUM.sub("", s)
    s = s.lstrip("・･-－—")
    return s


def _cand(code, name):
    return {"order": int(code[0]), "family": int(code[1]), "genus": int(code[2]), "species": int(code[3]),
            "variety": int(code[4]), "property": int(code[5]), "variety_name": name}


def _dedupe(cands):
    out, seen = [], set()
    for c in cands:
        k = (c["order"], c["family"], c["genus"], c["species"], c["variety"])
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
    return out


def _search(db, label, code, between):
    """1つの範囲で、エンジンと同じ順に候補を探す"""
    # 1. 完全一致
    exact = db.search_variety_codes(label, code=code, between=between)
    if exact:
        return [_cand(c, label) for c in exact]
    # 2. 類似(0.7) → コード化
    out = []
    for t in db.search_variety_title(label, ratio=0.7, code=code, between=between):
        c = db.search_variety_code(t, code=code, between=between)
        if c:
            out.append(_cand(c, t))
    if out:
        return out
    # 3. 文字の含まれ方(add_candidate_syou)
    syou = add_candidate_syou(db, code_o=code, between_o=between, val=label)
    if syou:
        return syou
    # 4. 類似(0.2)
    for t in db.search_variety_title(label, ratio=0.2, code=code, between=between)[:3]:
        c = db.search_variety_code(t, code=code, between=between)
        if c:
            out.append(_cand(c, t))
    return out


def match(label, scopes, limit=5):
    """科目名 → (候補リスト, 一致度の文字列 '0.85')

    scopes: [(code, between), ...] を狭い順に。最初に候補が見つかった範囲の結果を使う。
    """
    db = get_db()
    norm = normalize_label(label)
    if not norm:
        return [], "0.00"
    for code, between in scopes:
        cands = _dedupe(_search(db, norm, tuple(code), tuple(between)))
        if cands:
            cands = cands[:limit]
            r = difflib.SequenceMatcher(None, remove_brackets.sub(r"\1", norm), cands[0]["variety_name"]).ratio()
            return cands, "%.2f" % r
    return [], "0.00"


def search_variety_title_syou(title):
    return get_db().search_variety_title_syou(title)


def search_variety_code(title, code=(), between=()):
    return get_db().search_variety_code(title, code=code, between=between)
