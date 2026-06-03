FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libpq-dev && rm -rf /var/lib/apt/lists/*
COPY web/requirements.txt ./
RUN echo "151.101.192.223 pypi.org" >> /etc/hosts && \
    echo "167.82.48.223 files.pythonhosted.org" >> /etc/hosts && \
    pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt
COPY web/ ./
COPY shared/ ./shared/
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
