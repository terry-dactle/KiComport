ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_IMAGE}

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

# Install KiCad CLI tooling for in-container validation.
RUN apt-get update \
    && apt-get install -y --no-install-recommends kicad-cli ca-certificates \
    && rm -rf /var/lib/apt/lists/*

EXPOSE 27888

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "27888"]
