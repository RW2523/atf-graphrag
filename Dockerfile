FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV IGR_PROFILE=local
ENV IGR_PORT=8077
EXPOSE 8077

CMD ["python", "-m", "intelligraphrag", "serve"]
