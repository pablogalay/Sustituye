FROM python:3.13-slim
WORKDIR /app
ENV PYTHONPATH=/app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["sh", "-c", "python -m alembic upgrade head && python -m app.seed && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"]
