FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN date -u +build-%Y%m%d-%H%M%S > VERSION

VOLUME /data
ENV DB_PATH=/data/biblioteca.db

EXPOSE 5000

CMD ["gunicorn", "-b", "0.0.0.0:5000", "-w", "2", "app:app"]
