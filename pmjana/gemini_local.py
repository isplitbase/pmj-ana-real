# -*- coding: utf-8 -*-
"""Gemini 呼び出し(ana-gemini-real の写し)

ana の callgpt_Gemini.py は Cloud Run の ana-gemini(-real) に {prompt, image} を送り、
そこで Gemini を呼んでいる。pmj-ana は ana の本番サービスに依存しないよう、
同じ処理(ana/git/ana-gemini-real/main.py の gemini_proxy)をこのプロセスの中で行う。

環境変数(ana-gemini-real と同じ名前・既定値):
  GEMINI_API_KEY(または GOOGLE_API_KEY) … 必須
  GEMINI_MODEL              既定 models/gemini-3.1-pro-preview(無ければ近い安定版へ自動で切り替え)
  GEMINI_TEMPERATURE        既定 0.0
  GEMINI_THINKING_BUDGET    既定 0
  GEMINI_MAX_OUTPUT_TOKENS  既定 32768
  GEMINI_MAX_RETRIES        既定 5
"""
import base64
import io
import os
import time

from PIL import Image


def _get_api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY が未設定です")
    return key


def _decode_image(image_str: str) -> Image.Image:
    if not isinstance(image_str, str) or not image_str:
        raise ValueError("image is required")
    if image_str.startswith("data:"):
        try:
            _, b64data = image_str.split(",", 1)
        except ValueError:
            raise ValueError("Invalid data URI")
    else:
        b64data = image_str
    return Image.open(io.BytesIO(base64.b64decode(b64data)))


def _extract_text_from_response(resp) -> str:
    try:
        t = getattr(resp, "text", None)
        if isinstance(t, str) and t.strip():
            return t.strip()
    except Exception:
        pass
    texts = []
    for c in getattr(resp, "candidates", None) or []:
        content = getattr(c, "content", None)
        if not content:
            continue
        for p in getattr(content, "parts", None) or []:
            txt = getattr(p, "text", None)
            if isinstance(txt, str) and txt.strip():
                texts.append(txt.strip())
    return "\n".join(texts).strip()


def _response_debug_info(resp) -> dict:
    info = {}
    candidates = getattr(resp, "candidates", None)
    if candidates:
        fr = getattr(candidates[0], "finish_reason", None)
        info["finish_reason"] = str(fr) if fr is not None else None
    pf = getattr(resp, "prompt_feedback", None)
    if pf is not None:
        info["prompt_feedback"] = str(pf)
    return info


def gemini_generate(prompt: str, image_data_uri: str, timeout_sec: int = 180) -> str:
    """ana-gemini-real の gemini_proxy と同じ処理。成功したら本文、失敗したら例外。

    (timeout_sec は元の Cloud Run 呼び出しの引数。SDK 側の既定タイムアウトを使うので未使用)
    """
    from google import genai
    from google.genai import types as genai_types
    from pmjana.model_resolver import resolve_model

    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt is required")
    img = _decode_image(image_data_uri)

    model_name = os.environ.get("GEMINI_MODEL", "models/gemini-3.1-pro-preview")
    temperature = float(os.environ.get("GEMINI_TEMPERATURE", "0.0"))
    thinking_budget = int(os.environ.get("GEMINI_THINKING_BUDGET", "0"))
    max_output_tokens = int(os.environ.get("GEMINI_MAX_OUTPUT_TOKENS", "32768"))
    max_retries = int(os.environ.get("GEMINI_MAX_RETRIES", "5"))

    client = genai.Client(api_key=_get_api_key())
    model_name = resolve_model("gemini", model_name, client=client)
    config = genai_types.GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        thinking_config=genai_types.ThinkingConfig(thinking_budget=thinking_budget),
    )

    last_debug = {}
    for attempt in range(max_retries + 1):
        try:
            try:
                resp = client.models.generate_content(model=model_name, contents=[prompt, img], config=config)
            except Exception as e:
                # gemini-3.x-pro などは思考モード必須で thinking_budget=0 を受け付けない。
                # その場合は思考の量を指定せず(モデルの既定で)呼び直す
                if "only works in thinking mode" in str(e) and config.thinking_config is not None:
                    print("[gemini_local] 思考モード必須のモデルのため、thinking_budget を指定せずに呼び直します")
                    config = genai_types.GenerateContentConfig(temperature=temperature, max_output_tokens=max_output_tokens)
                    resp = client.models.generate_content(model=model_name, contents=[prompt, img], config=config)
                else:
                    raise
            text = _extract_text_from_response(resp)
            if text:
                return text
            last_debug = _response_debug_info(resp)
            last_debug["attempt"] = attempt + 1
            print(f"[gemini_local] empty text response, attempt {attempt + 1}/{max_retries + 1}, debug={last_debug}")
        except Exception as e:
            last_debug = {"exception": str(e), "attempt": attempt + 1}
            print(f"[gemini_local] exception on attempt {attempt + 1}/{max_retries + 1}: {e}")
        if attempt < max_retries:
            time.sleep(min(2 ** attempt * 0.5, 5.0))

    raise RuntimeError(f"Gemini から結果を得られませんでした: {last_debug}")
