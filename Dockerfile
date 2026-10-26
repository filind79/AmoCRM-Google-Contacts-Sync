FROM python:3.11-slim-trixie AS build
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && update-ca-certificates --fresh && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt constraints-https-20261026.txt ./
RUN pip install --user -r requirements.txt

FROM python:3.11-slim-trixie
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && update-ca-certificates --fresh && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=build /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH
COPY . .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
