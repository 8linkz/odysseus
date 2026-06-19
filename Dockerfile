FROM python:3.14-slim

# System deps. tmux is required by Cookbook for background downloads/serves.
# openssh-client is required for Cookbook remote server tests, setup, probes,
# downloads, and serves from Docker installs.
# git/cmake are required when Cookbook builds llama.cpp on first llama.cpp
# launch inside Docker.
# nodejs/npm provide npx for the optional built-in Browser MCP server.
# gosu lets the entrypoint drop privileges cleanly so signals still reach
# uvicorn directly (no extra shell layer like `su`/`sudo` would add).
# libmagic1 backs python-magic for content-based MIME detection on uploads;
# without it upload_handler falls back to extension-only guessing.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    curl \
    git \
    nodejs \
    npm \
    tmux \
    openssh-client \
    gosu \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# Docker CLI (client only — daemon stays on the host via the
# /var/run/docker.sock mount). The Debian `docker.io` package ships
# dockerd but not the client binary on slim, so grab the static client
# tarball from download.docker.com instead.
ARG DOCKER_CLI_VERSION=27.5.1
RUN ARCH="$(dpkg --print-architecture)" \
    && case "$ARCH" in \
         amd64) DARCH=x86_64 ;; \
         arm64) DARCH=aarch64 ;; \
         *) echo "unsupported arch $ARCH"; exit 1 ;; \
       esac \
    && curl -fsSL "https://download.docker.com/linux/static/stable/${DARCH}/docker-${DOCKER_CLI_VERSION}.tgz" \
       -o /tmp/docker.tgz \
    && tar -xzf /tmp/docker.tgz -C /tmp \
    && install -m 0755 /tmp/docker/docker /usr/local/bin/docker \
    && rm -rf /tmp/docker /tmp/docker.tgz

WORKDIR /app

# Install Python deps first (layer cache). Optional extras (PyMuPDF AGPL, etc.)
# are opt-in so the default image stays MIT-core; see requirements-optional.txt.
ARG INSTALL_OPTIONAL=false
COPY requirements.txt requirements-optional.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$INSTALL_OPTIONAL" = "true" ]; then pip install --no-cache-dir -r requirements-optional.txt; fi

# Bake the built-in Browser MCP (Playwright) into the image so browser-
# automation tools work out of the box and survive a container recreate. On by
# default; set --build-arg INSTALL_BROWSER=false to skip and keep the image
# lean (chromium + system libs add several hundred MB).
#
# Two things have to land where the *runtime* user (HOME=/app, dropped via
# gosu) will look for them, or the server silently stays unavailable:
#   - npm_config_cache=/app/.npm  -> builtin_mcp._npm_cache_roots() checks this
#     env first, so the @playwright/mcp npx package cache is found at runtime.
#   - PLAYWRIGHT_BROWSERS_PATH    -> shared by the build-time install and the
#     runtime MCP process so the exact chromium revision is reused.
# Chromium is installed via @playwright/mcp's *own* playwright dependency
# (npx --package=...), not a standalone playwright@latest, so the browser
# revision matches what the MCP launches — otherwise it re-downloads on first
# use. Neither /app/.npm nor /app/.cache/ms-playwright is a bind mount (see
# docker-compose.yml), so both survive a container recreate.
ARG INSTALL_BROWSER=true
ENV PLAYWRIGHT_BROWSERS_PATH=/app/.cache/ms-playwright \
    npm_config_cache=/app/.npm
RUN if [ "$INSTALL_BROWSER" = "true" ]; then \
        HOME=/app npx -y @playwright/mcp@latest --version \
        && HOME=/app npx -y --package=@playwright/mcp@latest -- playwright install --with-deps chromium \
        && chmod -R a+rX /app/.npm /app/.cache/ms-playwright \
        && rm -rf /var/lib/apt/lists/*; \
    fi

# Copy app code
COPY . .

# Create data directory (mount a volume here for persistence)
RUN mkdir -p data logs services/cache/search

# Entrypoint that drops to PUID/PGID (default 1000:1000) and repairs
# ownership on the bind-mounted /app/data and /app/logs. Without this,
# the container runs as root and writes root-owned files into host
# bind mounts — any later non-root run (or a host user trying to
# update them) silently fails on EPERM, breaking skill extraction,
# prefs persistence, mail attachments, etc.
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 7000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7000"]
