# Stage 1: build the frontend
FROM node:22-alpine AS frontend
WORKDIR /build/frontend
# Firebase web config is build-time; wire these to Railway build variables.
ARG VITE_FIREBASE_API_KEY
ARG VITE_FIREBASE_AUTH_DOMAIN
ARG VITE_FIREBASE_PROJECT_ID
ARG VITE_FIREBASE_APP_ID
ENV VITE_FIREBASE_API_KEY=$VITE_FIREBASE_API_KEY \
    VITE_FIREBASE_AUTH_DOMAIN=$VITE_FIREBASE_AUTH_DOMAIN \
    VITE_FIREBASE_PROJECT_ID=$VITE_FIREBASE_PROJECT_ID \
    VITE_FIREBASE_APP_ID=$VITE_FIREBASE_APP_ID
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: API plus the built frontend
FROM python:3.11-slim
WORKDIR /app/backend
COPY backend/pyproject.toml ./
# hatchling needs src/ present to build the package.
COPY backend/src ./src
RUN pip install --no-cache-dir .
COPY backend/migrations ./migrations
COPY backend/start.sh ./start.sh
RUN chmod +x start.sh
COPY --from=frontend /build/frontend/dist /app/frontend/dist
ENV FRONTEND_DIST=/app/frontend/dist
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"
# start.sh migrates, then execs: uvicorn src.main:app --host 0.0.0.0 --port 8000
CMD ["./start.sh"]
