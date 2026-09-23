FROM python:3.12-slim

WORKDIR /app
COPY app.py .
COPY index.html .
COPY data ./data
RUN pip install --no-cache-dir flask matplotlib

ENV API_KEY=change-me
ENV PORT=8420
ENV STALE_AFTER_SECONDS=30

EXPOSE 8420
CMD ["python", "app.py"]
