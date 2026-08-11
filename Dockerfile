# CARDINAL single public Cloud Run service: Vite build + FastAPI runtime
FROM node:20-alpine AS frontend-build
WORKDIR /web
COPY package*.json ./
RUN npm ci
COPY . .
# VITE_* values can still be supplied by your existing Vercel deployment.
# For Cloud Run the frontend code falls back to window.location.origin.
RUN npm run build

FROM python:3.10-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080 FRONTEND_DIST_DIR=/app/frontend_dist
WORKDIR /app
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY backend /app/backend
COPY --from=frontend-build /web/dist /app/frontend_dist
WORKDIR /app/backend
CMD ["sh","-c","uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
