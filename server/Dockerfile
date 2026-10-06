# Both stages must be the same image: the packages are installed for its
# Python version and copied into its site-packages. Dependabot bumps the
# digest in both lines together.

# The packages are installed for the target platform from prebuilt wheels,
# on the build machine's own CPU. Byte-compiled .pyc files don't depend on
# the CPU either, so an arm64 image never runs anything under emulation -
# pip under QEMU took over two minutes.
FROM --platform=$BUILDPLATFORM python:3.14-slim@sha256:f85c5697265c178cc6887276c55fe16cf3d14ca35c3df6a5eab3b360534a55d2 AS deps
ARG TARGETARCH
COPY requirements.txt .
# http-ece only ships an sdist. It's pure Python, so a wheel built here
# works on any platform; --only-binary then keeps everything else to wheels
# rather than building an sdist for the wrong CPU.
RUN set -eu; \
    case "$TARGETARCH" in \
        amd64) arch=x86_64 ;; \
        arm64) arch=aarch64 ;; \
        *) echo "unsupported TARGETARCH $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    py=$(python -c 'import sys; print("%d%d" % sys.version_info[:2])'); \
    site=$(python -c 'import sysconfig; print(sysconfig.get_path("purelib"))'); \
    pip wheel --no-cache-dir --no-deps -w /wheels "$(grep '^http-ece==' requirements.txt)"; \
    pip install --no-cache-dir --target "/deps$site" -r requirements.txt \
        --find-links /wheels --only-binary=:all: \
        --platform "manylinux_2_28_$arch" --platform "manylinux_2_17_$arch" \
        --python-version "$py" --implementation cp \
        --abi "cp$py" --abi abi3 --abi none; \
    rm -rf "/deps$site/bin" "/deps$site/share"; \
    python -m compileall -q "/deps$site"

FROM python:3.14-slim@sha256:f85c5697265c178cc6887276c55fe16cf3d14ca35c3df6a5eab3b360534a55d2

# No RUN steps in this stage: they'd need emulation for arm64.
COPY --from=deps /deps/ /
WORKDIR /app/data
WORKDIR /app
COPY app.py index.html ./
COPY gcm ./gcm
COPY static ./static

ENV PORT=8420
ENV STALE_AFTER_SECONDS=30
ENV SESSION_COOKIE_SECURE=1

EXPOSE 8420
CMD ["python", "app.py"]
