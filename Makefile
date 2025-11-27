PYTHON ?= python3
VENV ?= .venv
ACTIVATE = . $(VENV)/bin/activate

.PHONY: venv deps dev run test docker-build docker-run clean

venv:
	$(PYTHON) -m venv $(VENV)

deps: venv
	$(ACTIVATE) && pip install --upgrade pip && pip install -r requirements.txt

dev: deps
	$(ACTIVATE) && pip install -r requirements-dev.txt

run: deps
	$(ACTIVATE) && uvicorn app.main:app --reload

test: dev
	$(ACTIVATE) && pytest

docker-build:
	docker build -t kicomport .

docker-run:
	docker run -d -p 27888:27888 kicomport

clean:
	rm -rf $(VENV)
