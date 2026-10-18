FROM node:22-alpine AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    CNE_DATA_DIR=/data \
    CNE_DASHBOARD_HOST=0.0.0.0
WORKDIR /app
COPY pyproject.toml README.md requirements.txt ./
COPY cne/ ./cne/
RUN pip install --no-cache-dir .
COPY --from=frontend /app/frontend/dist ./frontend/dist
EXPOSE 8000
CMD ["python", "-m", "cne.deploy"]
