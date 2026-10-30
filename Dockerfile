FROM node:22-alpine AS web-build

WORKDIR /workspace
COPY web/package*.json ./web/
RUN cd web && npm ci
COPY web ./web
RUN cd web && npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /workspace

COPY pyproject.toml README.md ./
COPY app ./app
COPY --from=web-build /workspace/app/static ./app/static
COPY migrations ./migrations
COPY scripts ./scripts
COPY alembic.ini ./

RUN pip install --no-cache-dir .

CMD ["python", "-m", "app.api.main"]
