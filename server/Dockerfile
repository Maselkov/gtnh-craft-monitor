# Both stages must be the same image: the packages are installed for its
# Python version and copied into its site-packages. Dependabot bumps the
# digest in both lines together.

# The packages are installed for the target platform from prebuilt wheels,
# on the build machine's own CPU. Byte-compiled .pyc files don't depend on
# the CPU either, so an arm64 image never runs anything under emulation -
# pip under QEMU took over two minutes.
FROM --platform=$BUILDPLATFORM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928 AS deps
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

FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

# No RUN steps in this stage: they'd need emulation for arm64.
COPY --from=deps /deps/ /
WORKDIR /app/data
WORKDIR /app
COPY app.py index.html ./
COPY gcm ./gcm
COPY static ./static

# The release this image is, from publish.yml; the Status dialog shows it
# and auto-update needs it. After the COPYs, so a new tag reuses their layers.
ARG GCM_VERSION=
ENV GCM_VERSION=$GCM_VERSION
# The oldest release with this one's in-game scripts, also from
# publish.yml: computers on an older one are asked to update.
ARG GCM_SCRIPTS_VERSION=
ENV GCM_SCRIPTS_VERSION=$GCM_SCRIPTS_VERSION
ENV PORT=8420
ENV STALE_AFTER_SECONDS=30
# glibc gives threads their own malloc heaps, up to 8 per core, and memory
# freed in one is only reused by its threads. Two shared by all of
# waitress's threads saved ~5 MB of a big base's ~110; the lock they share
# hardly matters under the GIL. See gcm/memory.py.
ENV MALLOC_ARENA_MAX=2
ENV SESSION_COOKIE_SECURE=1

EXPOSE 8420
CMD ["python", "app.py"]
