# the packages for the target arch are put together on the build machine (no emulation): only wheels, for the
# image's Python and glibc (2.41: manylinux_2_17 to _2_41); the Rust core (presence_core) is the wheel CI built
# into dist/<arch> (docs/rust-core.md 5)
FROM --platform=$BUILDPLATFORM python:3.13-slim AS packages
ARG BUILD_ARCH
COPY dist/${BUILD_ARCH} /tmp/dist
# numpy: the calibration's data (npz) and the frames' positions
RUN machine=$(echo "$BUILD_ARCH" | sed 's/^amd64$/x86_64/') \
    && pip install --no-cache-dir --root-user-action=ignore --target /packages --only-binary=:all: \
        --python-version 3.13 --implementation cp $(for v in $(seq 17 41); do echo --platform manylinux_2_${v}_$machine; done) \
        /tmp/dist/presence_core-*.whl "numpy>=2.0" "aiomqtt>=2.3" "aiohttp>=3.10"

FROM python:3.13-slim
ARG BUILD_ARCH
ARG BUILD_VERSION
LABEL io.hass.type="addon" io.hass.arch="${BUILD_ARCH}" io.hass.version="${BUILD_VERSION}" \
      org.opencontainers.image.source="https://github.com/LeonRein/presence-tracker"

COPY --from=packages /packages /usr/local/lib/python3.13/site-packages
WORKDIR /app
COPY presence_tracker /app/presence_tracker

ENV PYTHONUNBUFFERED=1
# numpy computes nothing large any more (the core does): no idle OpenBLAS thread pool
ENV OPENBLAS_NUM_THREADS=1
CMD ["python", "-m", "presence_tracker"]
