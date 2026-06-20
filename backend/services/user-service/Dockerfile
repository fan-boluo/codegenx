# ============ User Service (gRPC) ============
FROM python:3.12-slim

WORKDIR /app

# Install deps
COPY backend/services/user-service/requirements.txt ./user-service/requirements.txt
RUN pip install --no-cache-dir -r user-service/requirements.txt

# Copy shared library
COPY backend/shared/ ./shared/
COPY backend/infra/ ./infra/

# Copy user-service code
COPY backend/services/user-service/ ./user-service/

WORKDIR /app/user-service
EXPOSE 50051

CMD ["python", "main.py"]
