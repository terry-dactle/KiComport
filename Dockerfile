FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 27888

# TODO: add kicad-cli installation in a later phase
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "27888"]
