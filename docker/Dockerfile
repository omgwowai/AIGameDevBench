# AIGameDevBench runner image.
# Builds the benchmark from source and bundles a headless Godot binary so the
# godot_scene_assert / godot_scenetree verifiers can run. Each container runs
# exactly ONE testcase (see entrypoint.sh); the k8s matrix script fans out one
# Job per testcase across the cluster.

FROM python:3.12-slim

# Godot version to bundle. The folder testcases declare project features for
# 4.2-4.5; a single 4.x headless binary loads all of them fine. Override with
#   docker build --build-arg GODOT_VERSION=4.4.1-stable ...
ARG GODOT_VERSION=4.5-stable

# Optional: install a real AI harness CLI at build time (DRIVER=command).
# Empty by default so the base image stays small and the golden-patch / noop
# drivers work with no extra deps. Example:
#   --build-arg HARNESS_INSTALL='npm i -g @anthropic-ai/claude-code'
ARG HARNESS_INSTALL=""

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Runtime deps: git (worktrees / git-type testcases + folder workspace init),
# unzip + ca-certificates + curl (fetch Godot), and the shared libs the Godot
# headless binary links against even in --headless mode.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl unzip ca-certificates \
        libx11-6 libxext6 libxcursor1 libxinerama1 libxrandr2 libxi6 \
        libgl1 libglu1-mesa libasound2 libpulse0 libfontconfig1 libxkbcommon0 \
    && rm -rf /var/lib/apt/lists/*

# --- Install headless Godot on PATH as `godot` -------------------------------
# Uses the official GitHub release zips. The _linux.x86_64 asset is the headless
# editor/runtime that supports --headless --script and scene execution.
RUN set -eux; \
    base="https://github.com/godotengine/godot/releases/download/${GODOT_VERSION}"; \
    zip="Godot_v${GODOT_VERSION}_linux.x86_64.zip"; \
    curl -fSL "${base}/${zip}" -o /tmp/godot.zip; \
    unzip -q /tmp/godot.zip -d /tmp/godot; \
    bin="$(find /tmp/godot -maxdepth 1 -type f -name 'Godot_v*_linux.x86_64' | head -n1)"; \
    install -m 0755 "$bin" /usr/local/bin/godot; \
    rm -rf /tmp/godot /tmp/godot.zip; \
    godot --headless --version

# --- Optional AI-harness CLI (DRIVER=command) --------------------------------
# Runs only when HARNESS_INSTALL is non-empty. Pull node in on demand so the
# common case (no harness) doesn't pay for it.
RUN set -eux; \
    if [ -n "$HARNESS_INSTALL" ]; then \
        apt-get update && apt-get install -y --no-install-recommends nodejs npm \
        && rm -rf /var/lib/apt/lists/*; \
        sh -c "$HARNESS_INSTALL"; \
    fi

# --- Build the benchmark from source -----------------------------------------
WORKDIR /app
# Copy metadata first for better layer caching, then the rest.
COPY pyproject.toml README.md ./
COPY src ./src
COPY testcases ./testcases
COPY testcases_filtered ./testcases_filtered
COPY tests ./tests
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

RUN python -m pip install -e ".[dev]" \
    && chmod +x /usr/local/bin/entrypoint.sh \
    && aigdbench --version

# --- Bake in the agentic-game-development plugin (DRIVER=command + claude) ----
# The build wrapper (scripts/build_runner_image.sh) vendors the plugin's latest
# skills into docker/vendor/agd-plugin BEFORE `docker build`, so this COPY picks
# up the current plugin. Two install paths, belt-and-suspenders:
#   1. personal skills at /root-of-HOME/.claude/skills  -> `claude -p` auto-loads
#      each SKILL.md by description (no settings.json, no marketplace).
#   2. the whole plugin at /opt/agd-plugin -> pass `--plugin-dir /opt/agd-plugin`
#      in HARNESS_CMD for a deterministic, namespaced load.
# HOME=/tmp (set below), so personal skills live at /tmp/.claude/skills.
# The build context is the AIGameDevBench repo root; vendor dir is gitignored.
COPY docker/vendor/agd-plugin /opt/agd-plugin
RUN mkdir -p /tmp/.claude/skills \
    && cp -r /opt/agd-plugin/skills/. /tmp/.claude/skills/ \
    && ls /tmp/.claude/skills

# Godot writes an editor config dir; give it a writable HOME.
ENV HOME=/tmp \
    TESTCASES_DIR=/app/testcases \
    OUT_DIR=/out \
    DRIVER=patch \
    GODOT_BINARY=godot

# /out may be bind-mounted or a k8s volume so per-testcase reports land outside
# the container. The entrypoint ALSO prints the report to stdout between markers
# so the orchestrator can collect results straight from `kubectl logs` with no
# shared storage. Each container runs a single testcase named by TESTCASE.
VOLUME ["/out"]
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
