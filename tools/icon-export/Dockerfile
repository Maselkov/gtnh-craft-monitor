# Headless GTNH icon/catalog export. Use run.sh rather than building this
# directly - it wires up the pack, cache and output mounts.
#
# The package list for running the client under Xvfb with Mesa software
# rendering follows gtnh-factory-flow's dataset pipeline Dockerfile
# (github.com/jackwrichards/gtnh-factory-flow, MIT).

# --- Build the mod. The GTNH buildscript needs JDK 25 to run Gradle. ---
# Layer caching skips this unless something under mod/ changed.
FROM eclipse-temurin:25-jdk AS mod
WORKDIR /mod
COPY mod/ ./
RUN ./gradlew --no-daemon build \
    && cp "$(ls build/libs/*.jar | grep -v -- '-dev.jar$' | head -n 1)" /gcmiconexport.jar

# --- Run the client. GTNH 2.8+ supports Java 17-25; 21 is the middle. ---
FROM eclipse-temurin:21-jre-jammy

ENV DEBIAN_FRONTEND=noninteractive
# SDL3 (lwjgl3ify's window backend) and Mesa's GLX/llvmpipe, under Xvfb.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        python3 \
        python3-pil \
        xauth \
        xvfb \
        libgl1 \
        libgl1-mesa-dri \
        libglx-mesa0 \
        libegl1 \
        libx11-6 \
        libxcursor1 \
        libxext6 \
        libxfixes3 \
        libxi6 \
        libxinerama1 \
        libxkbcommon0 \
        libxrandr2 \
        libxrender1 \
        libxss1 \
        libxtst6 \
        libxxf86vm1 \
        libopenal1 \
        libasound2 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=mod /gcmiconexport.jar /opt/gcm/gcmiconexport.jar
COPY export.py /opt/gcm/export.py

ENTRYPOINT ["python3", "/opt/gcm/export.py"]
