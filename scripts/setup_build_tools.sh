#!/usr/bin/env sh
# Installs what the site builder needs (backend/builder.py):
#   runtime/node_modules   react, react-dom, lucide-react, framer-motion (shared by every site)
#   runtime/bin/esbuild    bundler (native binary from the esbuild npm package)
#   runtime/bin/tailwindcss  Tailwind CSS v4 standalone CLI
# Usage: sh scripts/setup_build_tools.sh   (needs Node 18+ and curl)
set -eu
TAILWIND_VERSION="${TAILWIND_VERSION:-4.3.3}"
cd "$(dirname "$0")/../runtime"

npm install --no-audit --no-fund --omit=dev
mkdir -p bin

case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) os=windows ;;
  Darwin) os=macos ;;
  *) os=linux ;;
esac
case "$(uname -m)" in
  arm64|aarch64) arch=arm64 ;;
  *) arch=x64 ;;
esac

if [ "$os" = windows ]; then
  cp node_modules/@esbuild/win32-*/esbuild.exe bin/esbuild.exe
  asset="tailwindcss-windows-x64.exe"; out=bin/tailwindcss.exe
else
  cp node_modules/@esbuild/*/bin/esbuild bin/esbuild
  suffix=""
  if [ "$os" = linux ] && ldd --version 2>&1 | grep -qi musl; then suffix="-musl"; fi
  asset="tailwindcss-$os-$arch$suffix"; out=bin/tailwindcss
fi
curl -fsSL -o "$out" "https://github.com/tailwindlabs/tailwindcss/releases/download/v$TAILWIND_VERSION/$asset"
chmod +x bin/* 2>/dev/null || true
echo "Build tools ready in runtime/ (esbuild $(./bin/esbuild* --version), tailwindcss v$TAILWIND_VERSION)"
