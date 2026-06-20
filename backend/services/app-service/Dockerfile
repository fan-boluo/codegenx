# ============ App Service ============
FROM python:3.12-slim

WORKDIR /app

# Install deps
COPY backend/services/app-service/requirements.txt ./app-service/requirements.txt
RUN pip install --no-cache-dir -r app-service/requirements.txt

# Copy shared library
COPY backend/shared/ ./shared/
COPY backend/infra/ ./infra/

# Copy app-service code
COPY backend/services/app-service/ ./app-service/

WORKDIR /app/app-service
EXPOSE 8004

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8004"]
