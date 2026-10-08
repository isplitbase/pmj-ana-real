# -*- coding: utf-8 -*-
"""ana 本番の callgpt.py を、そのままの形で実行する

ana では do_ai.php が次のように callgpt.py をバックグラウンドで起動している。
  python callgpt.py "<画像|,|...>" "<種類|,|...>" "<回転|,|...>" "<期キー|,|...>" <ai_case_id>
pmj-ana でも同じ引数で起動し、結果は DB の代わりに shim(mysql.connector の差し替え)が
JSON ファイルに書き出したものを読む。

callgpt.py は設定ファイル pf(JSON) を自分と同じフォルダから読むので、
起動時に環境変数から pf を作る(API キーを含むので git には入れない。.gitignore 済み)。
"""
import json
import os
import subprocess
import sys
import uuid

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANA_DIR = os.path.join(APP_DIR, "ana")
SHIM_DIR = os.path.join(APP_DIR, "shim")
WORK_ROOT = os.environ.get("PMJANA_WORK", "/tmp/pmjana")
CALLGPT_TIMEOUT = int(os.environ.get("PMJANA_CALLGPT_TIMEOUT", "1500"))


def openai_keys():
    """OPENAI_API_KEYS(改行/カンマ区切り) と OPENAI_API_KEY を合わせたもの(重複除去)"""
    keys = []
    for line in (os.environ.get("OPENAI_API_KEYS") or "").splitlines():
        keys.extend([k.strip() for k in line.split(",") if k.strip()])
    single = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if single:
        keys.append(single)
    return list(dict.fromkeys(keys))


def write_pf():
    """callgpt.py / callgpt_Gemini.py が読む pf を作る(ana の pf と同じキー名)"""
    keys = openai_keys()
    pf = {
        # 読み取り
        "model_str": os.environ.get("OPENAI_MODEL", "gpt-4.1-mini-2025-04-14"),
        "openai_api_key": keys[0] if keys else "",
        "openai_api_keys": keys,
        "gemini_api_key": os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "",
        "aitext1": os.path.join(ANA_DIR, "aitext1"),
        "pys_dir": ANA_DIR + "/",                         # margelist などの置き場所(末尾に / が必要)
        # 画像の保存(GCS / S3)はしない: 空にすると callgpt.py はアップロードを飛ばす
        "gcs_bucket": "",
        "google_application_credentials": "",
        "s3_access_key": "", "s3_secret_key": "", "s3_region": "", "bucket_name": "",
        "uploadDir": WORK_ROOT + "/",
        # DB は shim が受け止める(接続先は使われない)
        "host": "pmjana-shim", "dbname": "pmjana", "username": "", "password": "",
        # 進捗ファイルの置き場所(www_dir + "progress/")
        "www_dir": WORK_ROOT + "/",
        "test_flag": "",
        "port": 8012,
    }
    os.makedirs(os.path.join(WORK_ROOT, "progress"), exist_ok=True)
    path = os.path.join(ANA_DIR, "pf")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(pf, f, ensure_ascii=False)
    os.chmod(path, 0o600)
    return path


def run_callgpt(image_paths, types, upload_key, job_dir):
    """callgpt.py を実行して、ana の DB に入るはずだった値を返す

    返り値: {"ok": bool, "response": {"BS":[],"PL":[],"販売費":[],"製造原価":[]}, "response_2": {...},
             "closing_date": ..., "closing_date_zenki": ..., "log_tail": "...", "error": "..."}
    """
    job_id = "pmjana_" + uuid.uuid4().hex[:12]
    out_path = os.path.join(job_dir, "callgpt_out.json")
    log_path = os.path.join(job_dir, "callgpt.log")
    args = [
        sys.executable, os.path.join(ANA_DIR, "callgpt.py"),
        "|,|".join(image_paths),
        "|,|".join(types),
        "|,|".join(["0"] * len(image_paths)),          # 回転なし(ana の画面で人が回転を指定する部分)
        "|,|".join([upload_key] * len(image_paths)),
        job_id,
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([SHIM_DIR, APP_DIR, env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    env["PMJANA_OUT"] = out_path
    env["PYTHONUNBUFFERED"] = "1"

    rc = None
    timed_out = False
    with open(log_path, "w", encoding="utf-8") as log:
        try:
            p = subprocess.run(args, cwd=ANA_DIR, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=CALLGPT_TIMEOUT)
            rc = p.returncode
        except subprocess.TimeoutExpired:
            timed_out = True

    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        log_text = f.read()
    tail = log_text[-6000:]

    if timed_out:
        return {"ok": False, "error": f"callgpt.py が {CALLGPT_TIMEOUT} 秒以内に終わりませんでした", "log_tail": tail}
    if not os.path.exists(out_path):
        # callgpt.py は失敗すると {"error":..., "traceback":...} を表示して終わる
        return {"ok": False, "error": f"callgpt.py が結果を出しませんでした(終了コード {rc})", "log_tail": tail}

    with open(out_path, "r", encoding="utf-8") as f:
        values = json.load(f)

    def _json(v, default):
        if isinstance(v, (dict, list)):
            return v
        try:
            return json.loads(v) if v else default
        except Exception:
            return default

    return {
        "ok": True,
        "response": _json(values.get("response"), {}),
        "response_2": _json(values.get("response_2"), {}),
        "closing_date": values.get("closing_date") or "",
        "closing_date_zenki": values.get("closing_date_zenki") or "",
        "closing_date_zenzenki": values.get("closing_date_zenzenki") or "",
        "log_tail": tail,
    }
