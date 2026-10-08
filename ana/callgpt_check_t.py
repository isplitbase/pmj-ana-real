from PIL import Image, ImageOps
import io
import base64
import json
import re

from openai import OpenAI


# =========================
# BS書式判定プロンプト（full/left/right前提・JSONのみ）
# =========================
BS_LAYOUT_INSTRUCTIONS = """
あなたは貸借対照表（BS: Balance Sheet）の書式（レイアウト）を判定する専門判定器である。
入力は同一ページの画像3枚である：
(1) full: 元画像全体
(2) left: 元画像の左半分
(3) right: 元画像の右半分

判定対象は次の2種類のみ。

【single_column】
- 資産→負債→純資産が縦方向に続く（同一の縦リスト）。
- 金額列が複数あっても、それが当期/前期、前年差、注記等の付随列なら single_column。

【t_format】
- 左半分(left)に資産の科目群が主体として存在し、
  右半分(right)に負債および純資産（または負債及び純資産）の科目群が主体として存在する。
- かつ、full画像で同一高さ帯に左右それぞれ別科目＋金額が並ぶ箇所が複数（目安3箇所以上）確認できる。

【最重要ルール】
- t_format と判定してよいのは「right画像に負債/純資産の科目名が実際に存在する」と確信できる場合のみ。
- right画像が数字列（当期/前期など）中心で、負債/純資産の科目名が確認できないなら single_column。
- 判断に迷う場合は必ず single_column。

【出力】
出力は必ずJSONのみ。余計な文章を一切出力しない。

{
  "format": "single_column" | "t_format",
  "confidence": 0.0,
  "evidence": {
    "left_has_asset_headers": false,
    "right_has_liability_equity_headers": false,
    "right_has_item_names": false,
    "right_item_name_examples": [],
    "full_same_row_left_right_item_amount_count_ge_3": false,
    "headers_found_full": []
  },
  "rationale": "100字程度で客観的に述べる"
}

right_item_name_examples は最大6件まで。right画像で確認できた科目名のみを書くこと。
"""


def _guess_mime_for_path(path: str) -> str:
    p = (path or "").lower()
    return "image/png" if p.endswith(".png") else "image/jpeg"


def _pil_to_data_url(img: Image.Image, mime: str) -> str:
    buf = io.BytesIO()
    if mime == "image/png":
        img.save(buf, format="PNG")
    else:
        img.save(buf, format="JPEG", quality=95)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def _image_file_to_three_data_urls(path: str):
    img = Image.open(path)
    # EXIF回転補正
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass

    w, h = img.size
    mid = w // 2

    full = img
    left = img.crop((0, 0, mid, h))
    right = img.crop((mid, 0, w, h))

    mime = _guess_mime_for_path(path)
    return (
        _pil_to_data_url(full, mime),
        _pil_to_data_url(left, mime),
        _pil_to_data_url(right, mime),
    )


def classify_bs_layout_by_gpt(
    image_path: str,
    api_key: str,
    model: str = "gpt-4.1-mini-2025-04-14"
) -> dict:
    """
    返り値例:
      {"format":"single_column"|"t_format", "confidence":..., "evidence":..., "rationale":...}
    失敗時は例外を投げる（呼び出し側で握りつぶしてOK）
    """
    if not api_key:
        raise ValueError("classify_bs_layout_by_gpt: api_key が空です")

    client = OpenAI(api_key=api_key)

    full_url, left_url, right_url = _image_file_to_three_data_urls(image_path)

    # あなたの最初のコードと同じ：Responses API + instructions + JSONのみ強制
    resp = client.responses.create(
        model=model,
        instructions=BS_LAYOUT_INSTRUCTIONS,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "3枚（full/left/right）を見てBSの書式を判定し、JSONのみを返してください。"},
                    {"type": "input_text", "text": "full:"},
                    {"type": "input_image", "image_url": full_url},
                    {"type": "input_text", "text": "left:"},
                    {"type": "input_image", "image_url": left_url},
                    {"type": "input_text", "text": "right:"},
                    {"type": "input_image", "image_url": right_url},
                ],
            }
        ],
        max_output_tokens=600,
        temperature=0.0,
    )

    raw = (resp.output_text or "").strip()
    if not raw:
        raise RuntimeError("BSレイアウト判定: モデル出力が空です")

    # 余計な文字が混ざっても JSON部分だけ拾う（安全策）
    m = re.search(r"\{.*\}", raw, flags=re.S)
    raw_json = m.group(0) if m else raw

    obj = json.loads(raw_json)
    obj["_raw_output"] = raw
    return obj
