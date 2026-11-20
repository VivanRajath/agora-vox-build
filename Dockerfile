# One container: builds the React frontend and the site-builder runtime, then
# serves the app, the API and the voice agent's /llm endpoint with uvicorn.

FROM node:20-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# Site builder (backend/builder.py): shared React deps, esbuild and the Tailwind v4 standalone CLI.
FROM node:20-slim AS site-runtime
ARG TAILWIND_VERSION=4.3.3
WORKDIR /app/runtime
COPY runtime/package.json runtime/package-lock.json ./
RUN npm ci --no-audit --no-fund --omit=dev \
 && mkdir bin \
 && cp node_modules/@esbuild/linux-*/bin/esbuild bin/esbuild \
 && arch="$(case "$(uname -m)" in aarch64|arm64) echo arm64 ;; *) echo x64 ;; esac)" \
 && node -e "fetch(process.argv[1]).then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.arrayBuffer(); }).then(b => require('fs').writeFileSync('bin/tailwindcss', Buffer.from(b)))" \
      "https://github.com/tailwindlabs/tailwindcss/releases/download/v${TAILWIND_VERSION}/tailwindcss-linux-${arch}" \
 && chmod +x bin/esbuild bin/tailwindcss \
 && ./bin/esbuild --version

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ backend/
COPY agents/ agents/
COPY --from=site-runtime /app/runtime runtime
COPY --from=frontend /app/frontend/dist frontend/dist
# Fail the image build, not the first voice edit, if the builder can't run here.
RUN ./runtime/bin/tailwindcss --help > /dev/null && ./runtime/bin/esbuild --version

# Render, Railway and Fly inject PORT; default to 8000 elsewhere.
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
