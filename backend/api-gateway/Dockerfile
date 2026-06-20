# ============ API Gateway ============
FROM python:3.12-slim

WORKDIR /app

# Install deps
COPY backend/api-gateway/requirements.txt ./api-gateway/requirements.txt
RUN pip install --no-cache-dir -r api-gateway/requirements.txt

# Copy shared library (needed by all services)
COPY backend/shared/ ./shared/
COPY backend/infra/ ./infra/

# Copy gateway code
COPY backend/api-gateway/ ./api-gateway/

WORKDIR /app/api-gateway
EXPOSE 8456

CMD ["python", "run.py", "--env", "prod", "--host", "0.0.0.0", "--port", "8456"]
