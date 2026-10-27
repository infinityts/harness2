FROM docker.io/library/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8443
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn src.main:app --host 0.0.0.0 --port 8443"]
