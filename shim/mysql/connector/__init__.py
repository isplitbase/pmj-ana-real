# -*- coding: utf-8 -*-
"""[pmj-ana] mysql.connector の差し替え

ana の callgpt.py は、読み取り結果を MySQL の ai_case テーブルに UPDATE して終わる。
pmj-ana には DB が無いので、その UPDATE の値を受け取って JSON ファイルに書き出す。
書き出し先は環境変数 PMJANA_OUT(runner.py が設定する)。

  ・UPDATE `ai_case` SET `response`=..., `response_2`=..., `sizes`=..., `status`=...,
      `closing_date`=..., `closing_date_zenki`=..., `closing_date_zenzenki`=..., `response_pdf`=...
    → この値を PMJANA_OUT に保存
  ・SELECT(response_2 の読み直し)は結果なし、OPTIMIZE / 古い行の掃除は何もしない
"""
import json
import os
import re


class Error(Exception):
    pass


_UPDATE_RE = re.compile(r"UPDATE\s+`?ai_case`?\s+SET(.*?)WHERE", re.S | re.I)
_COL_RE = re.compile(r"`?([A-Za-z_0-9]+)`?\s*=\s*%s")


class _Cursor:
    def __init__(self):
        self._rows = []

    def execute(self, sql, params=None):
        self._rows = []
        m = _UPDATE_RE.search(sql or "")
        if not m or params is None:
            return
        cols = _COL_RE.findall(m.group(1))
        if not cols or "response" not in cols:
            return
        values = dict(zip(cols, list(params)[:len(cols)]))
        out = os.environ.get("PMJANA_OUT")
        if out:
            with open(out, "w", encoding="utf-8") as f:
                json.dump(values, f, ensure_ascii=False)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return None

    def close(self):
        pass


class _Connection:
    def cursor(self, *args, **kwargs):
        return _Cursor()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass

    def is_connected(self):
        return True


def connect(*args, **kwargs):
    return _Connection()
