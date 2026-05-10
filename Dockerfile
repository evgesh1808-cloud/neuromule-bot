FROM python:3.11-slim

WORKDIR /app

RUN mkdir -p /app/data /app/logs

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

ENV PYTHONUNBUFFERED=1
CMD ["python", "main.py"]