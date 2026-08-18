FROM python:3.13-slim

WORKDIR /app
COPY app.py /app/app.py

RUN pip install --no-cache-dir --index-url https://pypi.org/simple/ \
    fastapi uvicorn "httpx[socks]"

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
