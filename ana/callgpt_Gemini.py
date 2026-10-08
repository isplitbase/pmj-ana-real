# callgpt_Gemini.py

import os
import json
import base64
from typing import List, Dict, Any, Optional, Tuple

import requests
from PIL import Image


# ----------------------------
# Cloud Run 呼び出し（Geminiをプロキシ）
# ----------------------------

# pf の値をキャッシュするためのモジュール変数
_CONFIG_CACHE: Optional[Dict[str, Any]] = None


def _load_pf_config() -> Dict[str, Any]:
    """
    同階層の `pf` ファイル(JSON)を読み込んでキャッシュする。
    読み込みに失敗した場合は空 dict を返す（呼び出し側で fallback できるように）。
    """
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE

    pf_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pf")
    try:
        with open(pf_path, "r", encoding="utf-8") as f:
            _CONFIG_CACHE = json.load(f)
    except Exception as e:
        print(f"[Gemini][WARN] pf の読み込みに失敗しました: {pf_path} ({e})")
        _CONFIG_CACHE = {}
    return _CONFIG_CACHE


def _get_cloudrun_url() -> str:
    """
    pf の port に応じて Cloud Run URL を切り替える。
      - port = 8012 → ana-gemini-real-... を呼ぶ
      - port = 8056 → ana-gemini-...      を呼ぶ
      - それ以外    → デフォルトとして ana-gemini-... を返す
    ★ URLとaudienceを完全一致させるため、末尾/付きで固定。
    """
    cfg = _load_pf_config()
    port = cfg.get("port")
    # JSON のパース結果が文字列の場合も拾う
    try:
        port_int = int(port) if port is not None else None
    except (TypeError, ValueError):
        port_int = None

    if port_int == 8012:
        return "https://ana-gemini-real-512697354748.asia-northeast1.run.app/"
    # 8056 もしくは判定不能時はデフォルト（既存URL）
    return "https://ana-gemini-512697354748.asia-northeast1.run.app/"


def _get_sa_json_path() -> Optional[str]:
    # ★ 固定でOK（運用が安定する）
    return "/srv/www/apps/analygent_backend/internal/gen-lang-client-0018414550-f50b079b0584.json"

def _get_id_token(audience: str) -> str:
    """
    gcloud無し環境(AWS EC2)で Cloud Run を叩くための IDトークンを生成。
    """
    sa_path = _get_sa_json_path()
    if not sa_path:
        raise RuntimeError(
            "Cloud Run が認証必須の場合、"
            "GOOGLE_APPLICATION_CREDENTIALS（または GCP_SERVICE_ACCOUNT_FILE）に"
            "サービスアカウント鍵(JSON)のパスを設定してください。"
        )

    import google.auth.transport.requests
    from google.oauth2 import service_account

    creds = service_account.IDTokenCredentials.from_service_account_file(
        sa_path,
        target_audience=audience,
    )
    req = google.auth.transport.requests.Request()
    creds.refresh(req)
    return creds.token


def _file_to_data_uri(image_path: str) -> Tuple[str, str]:
    """
    ファイルパス -> Data URI へ変換して返す（mimeも返す）
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"image_path が存在しません: {image_path}")

    img = Image.open(image_path)
    fmt = (img.format or "").upper()

    if fmt in ("JPEG", "JPG"):
        mime = "image/jpeg"
    elif fmt == "PNG":
        mime = "image/png"
    else:
        # 形式が曖昧ならPNGとして再保存して送る
        mime = "image/png"
        import io
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        return f"data:{mime};base64,{b64}", mime

    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")

    return f"data:{mime};base64,{b64}", mime


def _image_path_or_datauri_to_datauri(image_path: str) -> str:
    """
    入力が
      - Data URI (data:image/...;base64,....)
      - ファイルパス
    のどちらでも、最終的に Data URI を返す。
    """
    if isinstance(image_path, str) and image_path.startswith("data:"):
        return image_path
    data_uri, _mime = _file_to_data_uri(image_path)
    return data_uri


def _call_cloudrun_gemini(prompt: str, image_data_uri: str, timeout_sec: int = 180) -> str:
    # [pmj-ana] ana-gemini-real と同じ処理(pmjana/gemini_local.py)を直接呼ぶ。
    #   ana の本番サービスに依存しないようにするため。下の元の処理は使わない
    from pmjana.gemini_local import gemini_generate
    return gemini_generate(prompt, image_data_uri, timeout_sec=timeout_sec)

def _call_cloudrun_gemini_original(prompt: str, image_data_uri: str, timeout_sec: int = 180) -> str:
    url = _get_cloudrun_url()

    # ★ 認証必須なのでトークン生成失敗は握りつぶさない
    token = _get_id_token(url)

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    payload = {"prompt": prompt, "image": image_data_uri}

    r = requests.post(url, headers=headers, json=payload, timeout=timeout_sec)

    if r.status_code != 200:
        print("[CloudRun][HTTP ERROR] status:", r.status_code)
        print("[CloudRun][HTTP ERROR] body  :", r.text[:4000])
        raise RuntimeError(f"Cloud Run error: status={r.status_code}")

    data = r.json()

    if not data.get("ok"):
        print("[CloudRun][ERROR] ok=false:", data)
        raise RuntimeError(f"Cloud Run returned ok=false: {data}")

    return (data.get("text") or "").strip()


# ----------------------------
# JSON抽出の安全化（最長一致対策）
# ----------------------------

def _extract_first_json_object(text: str) -> str:
    """
    文字列中から「最初に現れる JSONオブジェクト（{...}）」を
    波括弧の対応を見ながら安全に抽出する。

    - 文字列中の { を起点に depth を数える
    - 文字列リテラル中の { } は無視（"..." 内、エスケープも考慮）
    - depth が 0 に戻った地点でそのオブジェクトを返す
    """
    if not isinstance(text, str) or not text:
        return ""

    start = text.find("{")
    if start < 0:
        return ""

    depth = 0
    in_string = False
    escape = False

    for i in range(start, len(text)):
        ch = text[i]

        if in_string:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = False
            continue

        # not in_string
        if ch == '"':
            in_string = True
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]

        # depth が負になるのは異常（壊れたテキスト）
        if depth < 0:
            break

    return ""


# ----------------------------
# 既存インタフェースを維持したまま差し替え
# ----------------------------

def analyze_pl_page_with_gemini(
    image_path: str,           # ← ファイルパス or Data URI
    page_no: int,
    api_key: str,
    base_prompt: str,
    extra_prompt: str = "",
) -> List[Dict[str, Any]]:
    """
    元コードの業務ロジックを壊さずに、
    Gemini呼び出しだけ Cloud Run 経由へ変更した版。

    改善点:
    - extra_prompt を必ず結合
    - JSON抽出を re最長一致から「括弧対応の取れた最初のJSON」に変更
    """

    # ✅ extra_prompt をちゃんと結合する（前回の改善）
    base = (base_prompt or "").strip()
    extra = (extra_prompt or "").strip()
    if extra:
        prompt = base + "\n\n" + extra
    else:
        prompt = base

    # 画像を Data URI に統一（Cloud Runへ渡す）
    image_data_uri = _image_path_or_datauri_to_datauri(image_path)

    # Cloud Runへ投げる（Gemini呼び出し部分）
    text = _call_cloudrun_gemini(prompt=prompt, image_data_uri=image_data_uri)
    print("===Gemini text ===\n")
    print(text)
    # ✅ JSON 部分だけ抽出（安全版）
    extracted = _extract_first_json_object(text)
    if extracted:
        text = extracted

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"[Gemini][WARN] JSON デコードに失敗しました: {e}")
        print(f"[Gemini][DEBUG] 先頭 200 文字: {text[:200]!r}")
        return []

    lst = data.get("list", [])
    if not isinstance(lst, list):
        print("[Gemini][WARN] JSON 中の 'list' が配列ではありません。")
        return []

    # page_no / type / 分類 などを補正して返却（元コード維持）
    normalized: List[Dict[str, Any]] = []

    for row in lst:
        if not isinstance(row, dict):
            continue

        account_name = row.get("勘定科目")
        if not isinstance(account_name, str):
            account_name = ""

        def _normalize_term(term_name: str) -> Dict[str, Any]:
            v = row.get(term_name)
            if isinstance(v, dict):
                amount = v.get("金額", "")
                term_page_no = v.get("page_no", page_no) or page_no
                return {"金額": amount, "page_no": term_page_no}
            else:
                return {"金額": v if v is not None else "", "page_no": page_no}

        now_term = _normalize_term("今期")
        prev_term = _normalize_term("前期")
        prev2_term = _normalize_term("前々期")

        row_type = row.get("type") or "PL"
        if not isinstance(row_type, str):
            row_type = "PL"

        bunrui = row.get("分類")
        if not isinstance(bunrui, str):
            bunrui = ""

        normalized.append(
            {
                "勘定科目": account_name,
                "今期": now_term,
                "前期": prev_term,
                "前々期": prev2_term,
                "type": row_type,
                "分類": bunrui,
            }
        )

    return normalized
