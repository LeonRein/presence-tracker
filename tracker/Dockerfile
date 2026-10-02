FROM python:3.13-alpine

WORKDIR /app
RUN pip install --no-cache-dir "numpy>=2.0" "aiomqtt>=2.3" "aiohttp>=3.10"
COPY presence_tracker /app/presence_tracker

ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "presence_tracker"]
