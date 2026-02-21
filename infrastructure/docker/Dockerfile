FROM python:3.11-slim

# System deps + Node.js 20 + Lighthouse CLI in a single layer to minimize size
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg ca-certificates fonts-liberation libnss3 libatk-bridge2.0-0 \
    libdrm2 libxkbcommon0 libgbm1 libasound2 libxshmfence1 \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g lighthouse@12 --no-fund --no-audit \
    && npm cache clean --force \
    && apt-get purge -y --auto-remove gnupg \
    && rm -rf /var/lib/apt/lists/* /tmp/* /root/.npm

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers
RUN playwright install chromium --with-deps

# Point Lighthouse at Playwright's Chromium — resolve glob at build time and create a stable symlink
RUN CHROME_REAL=$(ls /root/.cache/ms-playwright/chromium-*/chrome-linux/chrome 2>/dev/null | head -1) \
    && if [ -n "${CHROME_REAL}" ]; then \
         ln -sf "${CHROME_REAL}" /usr/local/bin/chromium-browser; \
         echo "CHROME_PATH=/usr/local/bin/chromium-browser" >> /etc/environment; \
       fi

ENV CHROME_PATH=/usr/local/bin/chromium-browser

# Copy crawl4ai source (with seo_audit layer)
# NOTE: The deploy script copies crawl4ai/ into this directory before build.
# See infrastructure/deploy.sh
COPY crawl4ai/ ./crawl4ai/

# Copy the HTTP server
COPY server.py .

EXPOSE 8000

CMD ["python", "server.py"]
