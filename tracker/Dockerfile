# glibc (Debian) instead of Alpine: numba's llvmlite has no musl wheels (MODEL.md 10, "Rechenzeit")
FROM python:3.13-slim

WORKDIR /app
RUN pip install --no-cache-dir --root-user-action=ignore "numpy>=2.0" "numba>=0.61" "aiomqtt>=2.3" "aiohttp>=3.10"
COPY presence_tracker /app/presence_tracker

ENV PYTHONUNBUFFERED=1
# numpy's OpenBLAS threads only wait on these small products: 7 % more CPU, no faster (6.10.)
ENV OPENBLAS_NUM_THREADS=1
# the compiled kernels (kernels.py) are cached when the image is built on the host: the app starts
# without compiling
ENV NUMBA_CACHE_DIR=/app/numba-cache
RUN python -m presence_tracker.kernels
CMD ["python", "-m", "presence_tracker"]
