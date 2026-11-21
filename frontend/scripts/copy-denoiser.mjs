// Agora's AI noise suppression loads two .wasm files at runtime; they must be served by the app
// (https://docs.agora.io/en/realtime-media/rtc/build/enhance-the-audio-experience/ai-noise-suppression/web).
// Copies them into public/denoiser so Vite serves them in dev and ships them in dist.
import { cpSync, existsSync, mkdirSync } from "node:fs";

const from = "node_modules/agora-extension-ai-denoiser/external";
const to = "public/denoiser";
if (existsSync(from)) {
  mkdirSync(to, { recursive: true });
  cpSync(from, to, { recursive: true });
}
