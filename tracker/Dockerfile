FROM python:3.13-slim

ARG BUILD_ARCH
ARG BUILD_VERSION
LABEL io.hass.type="addon" io.hass.arch="${BUILD_ARCH}" io.hass.version="${BUILD_VERSION}" \
      org.opencontainers.image.source="https://github.com/LeonRein/presence-tracker"

WORKDIR /app
# the Rust core (presence_core) as the wheel CI built into dist/ for this arch (docs/rust-core.md 5);
# numpy: the calibration's data (npz) and the frames' positions
COPY dist /tmp/dist
RUN pip install --no-cache-dir --root-user-action=ignore /tmp/dist/presence_core-*.whl \
        "numpy>=2.0" "aiomqtt>=2.3" "aiohttp>=3.10" \
    && rm -rf /tmp/dist
COPY presence_tracker /app/presence_tracker

ENV PYTHONUNBUFFERED=1
# numpy computes nothing large any more (the core does): no idle OpenBLAS thread pool
ENV OPENBLAS_NUM_THREADS=1
CMD ["python", "-m", "presence_tracker"]
