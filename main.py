# -*- coding: utf-8 -*-
"""pmj-ana : 強力分析 API (Cloud Run)

  zaiTask の「強力分析」で、決算書を Analygent(ana) と同じ方式で読み直し、
  PMJ の勘定科目マスタに当てはめた結果を、v2ac(iTaskScanPapers2)と同じ形で返す。

      ikisaki_itask_make_ana.do → pmj-door(-real) → [このサービス]
          → Azure OCR(ページ分類) / OpenAI / Gemini / Google Vision

  エンドポイント:
    GET  /         … ヘルスチェック
    POST /ping     … 設定(キー・マスタ)の確認。AI は呼ばない
    POST /analyze  … 分析

  POST /analyze の入力:
    {
      "images":   ["<base64 の JPEG/PNG>", ...],   # 必須。ページ順(data URI も可)
      "doc_type": "houjin" | "kojin",              # 既定 houjin
      "debug":    false                            # true なら ana の生の結果やログも返す
    }
  返り値(成功):
    { "status":"OK", "doc_type":"houjin",
      "result": { "format_info": { "cols": [ { "block_result": {
                    "closing_date": {"date":"2025/03/31", ...}, "company": {...}, "detail": [ ... ] } } ] },
                  "document_judgment_flag": "kojin"(個人のときだけ) },
      "pages": [{"no":1,"type":"BS or PL"}, ...], "upload_key": "...", "log": [...], "master": {...}, "elapsed": 123.4 }
  返り値(失敗): { "status":"NG", "error":"...", "log_tail":"..."(debug 時) }

  詳しくは DESIGN.md / README.md。
"""
import base64
import io
import os
import shutil
import threading
import time
import traceback
import uuid

from flask import Flask, jsonify, request
from PIL import Image

from pmjana import classify, houjin, kojin, master, period, runner

app = Flask(__name__)
SERVICE_NAME = os.environ.get("SERVICE_NAME", "pmj-ana-real")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini-2025-04-14")
MAX_PAGES = int(os.environ.get("PMJANA_MAX_PAGES", "30"))
_busy = threading.Lock()

runner.write_pf()


def _ng(msg, code=400, **extra):
    body = {"status": "NG", "error": msg}
    body.update(extra)
    return jsonify(body), code


def _decode(s):
    if not isinstance(s, str) or not s:
        raise ValueError("画像が空です")
    if s.startswith("data:"):
        s = s.split(",", 1)[1]
    return base64.b64decode(s)


def _block_result(detail, closing_date):
    return {
        "closing_date": {"date": closing_date, "page": "", "start_x": 0, "start_y": 0, "end_x": 0, "end_y": 0},
        "company": {"candidate": [], "page": 0, "start_x": 0, "start_y": 0, "end_x": 0, "end_y": 0},
        "detail": detail,
    }


@app.get("/")
def health():
    return jsonify({"status": "ok", "service": SERVICE_NAME})


@app.post("/ping")
def ping():
    info = {
        "status": "OK", "service": SERVICE_NAME,
        "openai_keys": len(runner.openai_keys()),
        "gemini_key": bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")),
        "azure_ocr": bool(classify.AZURE_KEY and classify.AZURE_ENDPOINT),
        "azure_cv": bool(os.environ.get("AZURE_API_KEY")),
        "openai_model": OPENAI_MODEL,
        "gemini_model": os.environ.get("GEMINI_MODEL", "models/gemini-3.1-pro-preview"),
    }
    try:
        master.get_db()
        info["master"] = master.master_info()
    except Exception as e:
        info["status"] = "NG"
        info["master_error"] = str(e)[:300]
    return jsonify(info)


@app.post("/analyze")
def analyze():
    t0 = time.time()
    body = request.get_json(silent=True) or {}
    images = body.get("images") or []
    doc_type = "kojin" if body.get("doc_type") == "kojin" else "houjin"
    debug = bool(body.get("debug"))
    if not isinstance(images, list) or not images:
        return _ng("images がありません")
    if len(images) > MAX_PAGES:
        return _ng("ページ数が多すぎます(%d ページまで)" % MAX_PAGES)
    if not runner.openai_keys():
        return _ng("OPENAI_API_KEY が未設定です", 500)
    if not _busy.acquire(blocking=False):
        return _ng("分析中です。しばらくしてから再実行してください", 429)

    job_dir = os.path.join(runner.WORK_ROOT, "job_" + uuid.uuid4().hex[:12])
    os.makedirs(job_dir, exist_ok=True)
    try:
        master.get_db()                     # マスタが読めなければ AI を呼ぶ前に止める

        # ページ画像を保存(ana の pdf-converter は PNG で callgpt.py に渡すので PNG にそろえる)
        jpg_paths, png_paths = [], []
        for i, s in enumerate(images):
            raw = _decode(s)
            jp = os.path.join(job_dir, "page_%03d.jpg" % (i + 1))
            with open(jp, "wb") as f:
                f.write(raw)
            jpg_paths.append(jp)
            pp = os.path.join(job_dir, "page_%03d.png" % (i + 1))
            Image.open(io.BytesIO(raw)).convert("RGB").save(pp, format="PNG")
            png_paths.append(pp)

        if doc_type == "kojin":
            data = kojin.read_with_openai(jpg_paths, runner.openai_keys()[0], OPENAI_MODEL)
            detail, kessan, log = kojin.build_detail(data)
            result = {"format_info": {"cols": [{"col_id": "", "col_name": "", "itask_form_id": "",
                                                "block_result": _block_result(detail, houjin.normalize_date(kessan))}]},
                      "document_judgment_flag": "kojin"}
            out = {"status": "OK", "doc_type": doc_type, "result": result, "log": log,
                   "master": master.master_info(), "elapsed": round(time.time() - t0, 1)}
            if debug:
                out["raw"] = data
            return jsonify(out)

        # ---- 法人 ----
        # 1. ページの種類(pdf-converter と同じ)
        cls = classify.classify_pages(png_paths)
        types = cls["types"]
        target = [p for p, t in zip(png_paths, types) if t in ("1", "3")]
        if not target:
            return _ng("決算書(貸借対照表・損益計算書・販管費)のページが見つかりませんでした",
                       pages=[{"no": i + 1, "type": n} for i, n in enumerate(cls["names"])])
        # 2. 今期だけか、今期・前期か(getpdfinfo と同じ)
        log = []
        try:
            per = period.detect_upload_key([p for p, t in zip(png_paths, types) if t == "1"] or target,
                                           runner.openai_keys()[0])
            upload_key = per["upload_key"]
            log.append("期の判定: %s → %s" % (per["labels"], upload_key))
        except Exception as e:
            upload_key = "currentTermBalanceSheet"
            log.append("期の判定に失敗したため今期のみとして読み取り: %s" % str(e)[:200])
        # 3. 読み取り(ana 本番の callgpt.py)
        r = runner.run_callgpt(png_paths, types, upload_key, job_dir)
        if not r["ok"]:
            extra = {"log_tail": r.get("log_tail", "")} if debug else {}
            return _ng(r["error"], 500, **extra)
        # 4. 勘定科目マスタとの突き合わせ → v2ac と同じ形
        detail, mlog = houjin.build_detail(r["response"])
        log.extend(mlog)
        if not detail:
            return _ng("読み取れた勘定科目がありませんでした", 500)
        result = {"format_info": {"cols": [{"col_id": "", "col_name": "", "itask_form_id": "",
                                            "block_result": _block_result(detail, houjin.normalize_date(r["closing_date"]))}]}}
        out = {"status": "OK", "doc_type": doc_type, "result": result, "upload_key": upload_key,
               "pages": [{"no": i + 1, "type": n} for i, n in enumerate(cls["names"])],
               "log": log, "master": master.master_info(), "elapsed": round(time.time() - t0, 1)}
        if debug:
            out["ana_response"] = r["response"]
            out["ana_closing_date"] = [r["closing_date"], r["closing_date_zenki"]]
            out["log_tail"] = r["log_tail"]
        return jsonify(out)

    except ValueError as e:
        return _ng(str(e), 400)
    except Exception as e:
        traceback.print_exc()
        return _ng("分析中にエラー: %s" % str(e)[:500], 500, **({"traceback": traceback.format_exc()[-3000:]} if debug else {}))
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)
        _busy.release()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
