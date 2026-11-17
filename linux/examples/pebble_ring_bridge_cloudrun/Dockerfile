FROM python:3.12-slim AS base
ARG MUSE_SDK_COMMIT=08f89ed329918c27d53b11192065ad6b2cb0e868
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8080
WORKDIR /app
RUN apt-get update \
 && apt-get install --no-install-recommends -y ffmpeg \
 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
 && python -c "import urllib.request; urllib.request.urlretrieve('https://github.com/facebookincubator/muse-gadget-sdk/archive/${MUSE_SDK_COMMIT}.tar.gz','/tmp/sdk.tar.gz')" \
 && mkdir /tmp/muse-sdk \
 && tar -xzf /tmp/sdk.tar.gz -C /tmp/muse-sdk --strip-components=1 \
 && pip install --no-cache-dir --no-deps /tmp/muse-sdk/linux \
 && rm -rf /tmp/muse-sdk /tmp/sdk.tar.gz \
 && useradd --uid 10001 --create-home bridge

FROM base AS checks
RUN pip install --no-cache-dir pytest==9.2.0
COPY audio.py audio_test.py pebble_ring_bridge.py pebble_ring_bridge_test.py ./
RUN python -m pytest -q audio_test.py pebble_ring_bridge_test.py

FROM base
# Use the exact shared parser and decoder sources that passed their tests.
COPY --from=checks --chown=bridge:bridge /app/audio.py /app/pebble_ring_bridge.py ./
COPY --chown=bridge:bridge app.py muse_sender.py ./
USER bridge
RUN python -c "from app import app; assert app.test_client().get('/health').status_code == 200"
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT} --workers 1 --threads 1 --timeout 110 --graceful-timeout 20 --access-logfile /dev/null --error-logfile - app:app"]
