# ============ AI Service ============
FROM python:3.12-slim

WORKDIR /app

# Install deps
COPY backend/services/ai-service/requirements.txt ./ai-service/requirements.txt
RUN pip install --no-cache-dir -r ai-service/requirements.txt

# Copy shared library
COPY backend/shared/ ./shared/
COPY backend/infra/ ./infra/

# Copy ai-service code (including bot/, monitor/, core/, guardrail/ etc.)
COPY backend/services/ai-service/ ./ai-service/

WORKDIR /app/ai-service
EXPOSE 8002

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8002"]
