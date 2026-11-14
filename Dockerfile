FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir fastapi uvicorn pandas "anthropic>=2.0" httpx python-dotenv jinja2 python-multipart
COPY app ./app
COPY data/persona.csv data/benchmark.json data/market.json data/statfin_benchmark.json ./data/
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
