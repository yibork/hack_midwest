# ---- 1. Build the React frontend
FROM node:22-alpine AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- 2. Python app serving the API and the built frontend
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
COPY --from=frontend /app/frontend/dist /app/frontend/dist
ENV FRONTEND_DIST=/app/frontend/dist SNAPSHOT_DIR=/app/data/snapshots
EXPOSE 8000
# Tables are created and demo data seeded on startup (app/main.py lifespan).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
