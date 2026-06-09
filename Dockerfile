FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV ATF_PROFILE=local
ENV ATF_PORT=8077
EXPOSE 8077

CMD ["python", "-m", "atf_graphrag", "serve"]
