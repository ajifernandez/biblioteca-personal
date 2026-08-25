VENV := venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: venv run run-https docker-up docker-down clean

venv:
	python3 -m venv $(VENV)
	$(PIP) install -q -r requirements.txt

run: venv
	DB_PATH=./data/biblioteca.db $(PYTHON) app.py

# HTTPS con certificado autofirmado: necesario para probar la cámara
# desde el móvil por IP local (192.168.x.x), ya que los navegadores
# bloquean getUserMedia fuera de localhost/HTTPS.
run-https: venv
	$(PIP) install -q pyopenssl
	HTTPS_ADHOC=1 DB_PATH=./data/biblioteca.db $(PYTHON) app.py

docker-up:
	docker compose up -d --build

docker-down:
	docker compose down

clean:
	rm -rf $(VENV) data __pycache__
