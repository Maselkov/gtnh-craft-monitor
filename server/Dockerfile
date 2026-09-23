FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY index.html .
COPY data ./data

ENV PORT=8420
ENV STALE_AFTER_SECONDS=30
ENV SESSION_COOKIE_SECURE=1

EXPOSE 8420
CMD ["python", "app.py"]
