FROM python:3.12-slim
WORKDIR /app

# Dependencies first (cached layer): only the manifest is needed here.
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e "."

# Runtime code ONLY — never `COPY . .`: that would bake backend/.env (live
# provider keys + JWT secret), .venv/, var/outbox, logs and caches into the
# image (audit P0-4). .dockerignore is the second line of defence.
COPY app ./app
COPY alembic ./alembic
COPY scripts ./scripts
COPY alembic.ini ./alembic.ini

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
