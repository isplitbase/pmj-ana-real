FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    TIKTOKEN_CACHE_DIR=/app/.tiktoken

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
 && python -c "import tiktoken; tiktoken.get_encoding('cl100k_base'); tiktoken.get_encoding('o200k_base')" || true

COPY . ./

# 1件の分析で数分かかり、メモリも使うため 1 プロセス 1 スレッド(Cloud Run の同時実行数も 1 にする)
CMD exec gunicorn -b :$PORT -w 1 --threads 1 -t 1800 main:app
