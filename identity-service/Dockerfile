FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# F119-s: run as a non-root user (defense-in-depth against container escape)
RUN useradd --create-home --uid 10001 appuser
USER appuser
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8001}
