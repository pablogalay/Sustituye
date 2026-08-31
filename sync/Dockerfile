FROM mcr.microsoft.com/playwright/python:v1.49.0-noble
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY educamadrid_sync ./educamadrid_sync
COPY config ./config

ENV EDUCAMADRID_HEADLESS=true
EXPOSE 8080
CMD ["uvicorn", "educamadrid_sync.server:app", "--host", "0.0.0.0", "--port", "8080"]
