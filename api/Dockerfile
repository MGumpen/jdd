FROM python:3.12-slim

WORKDIR /srv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY tests ./tests
COPY pytest.ini .

EXPOSE 8000
# Én uvicorn-prosess: bakgrunnsjobben startes av appen selv, og skal bare kjøre én gang.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
