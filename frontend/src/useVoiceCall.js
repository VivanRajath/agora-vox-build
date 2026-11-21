import { useCallback, useEffect, useRef, useState } from "react";

// The SDK is large, so it loads on demand (prefetched once a project is open).
let sdk = null;
function loadAgora() {
  sdk ??= import("agora-rtc-sdk-ng").then((m) => {
    m.default.setLogLevel(3); // warnings and errors only
    return m.default;
  });
  return sdk;
}

// Agora's AI noise suppression: cleans the mic (fans, traffic, background chatter) before it is sent,
// so speech recognition hears the user. Desktop Chrome/Edge/Firefox; skipped where unsupported.
let denoiser = null;
function loadDenoiser(AgoraRTC) {
  denoiser ??= import("agora-extension-ai-denoiser")
    .then(({ AIDenoiserExtension }) => {
      const ext = new AIDenoiserExtension({ assetsPath: "/denoiser" });
      if (!ext.checkCompatibility()) return null;
      AgoraRTC.registerExtensions([ext]);
      return ext;
    })
    .catch(() => null);
  return denoiser;
}

async function cleanMic(AgoraRTC, mic) {
  const ext = await loadDenoiser(AgoraRTC);
  if (!ext) return false;
  try {
    const processor = ext.createProcessor();
    mic.pipe(processor).pipe(mic.processorDestination);
    await processor.enable();
    return true;
  } catch {
    return false; // the call still works, just without the extra cleaning
  }
}

const MIC_ERRORS = new Set(["PERMISSION_DENIED", "NOT_READABLE", "DEVICE_NOT_FOUND"]);

function friendly(e) {
  if (MIC_ERRORS.has(e?.code) || e?.name === "NotAllowedError") {
    return "Microphone access is blocked. Allow it in your browser and try again.";
  }
  return e?.message || "Couldn't start the voice call";
}

async function post(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
  return data;
}

// state: idle | connecting | live | ending | error
export function useVoiceCall(projectId) {
  const session = useRef(null); // { client, mic, agentId }
  const [state, setState] = useState("idle");
  const [volume, setVolume] = useState(0); // the agent's voice, 0..1
  const [micLevel, setMicLevel] = useState(0); // the user's microphone, 0..1
  const [micLabel, setMicLabel] = useState("");
  const [micSilent, setMicSilent] = useState(false); // live for a while and the mic never picked anything up
  const [muted, setMuted] = useState(false);
  const [denoised, setDenoised] = useState(false); // AI noise suppression is cleaning the mic
  const [error, setError] = useState(null);

  const teardown = useCallback(async (notify = true) => {
    const s = session.current;
    session.current = null;
    if (!s) return;
    clearInterval(s.meter);
    s.mic?.close();
    await s.client.leave().catch(() => {});
    if (notify && s.agentId) post("/api/voice/stop", { agent_id: s.agentId }).catch(() => {});
    setVolume(0);
    setMicLevel(0);
    setMicSilent(false);
    setMuted(false);
  }, []);

  // Must be called straight from a click handler, or browsers block audio.
  const start = useCallback(async () => {
    if (session.current || !projectId) return;
    setError(null);
    setState("connecting");
    const AgoraRTC = await loadAgora();
    const client = AgoraRTC.createClient({ mode: "rtc", codec: "vp8" });
    const s = { client, mic: null, agentId: null, meter: null, agentTrack: null };
    session.current = s;
    try {
      // Mic first, so a blocked microphone doesn't start an agent we can't talk to.
      s.mic = await AgoraRTC.createMicrophoneAudioTrack({ AEC: true, ANS: true, AGC: true });
      s.denoised = await cleanMic(AgoraRTC, s.mic);
      setDenoised(s.denoised);
      const info = await post("/api/voice/start", { project_id: projectId });
      s.agentId = info.agent_id;
      if (session.current !== s) return; // stopped while connecting

      client.on("user-published", async (user, mediaType) => {
        if (mediaType !== "audio") return;
        await client.subscribe(user, mediaType);
        user.audioTrack.play();
        s.agentTrack = user.audioTrack;
      });
      client.on("user-unpublished", (user) => {
        if (s.agentTrack === user.audioTrack) s.agentTrack = null;
      });
      client.on("connection-state-change", (cur) => {
        if (cur === "DISCONNECTED" && session.current === s) {
          teardown();
          setState("idle");
        }
      });

      await client.join(info.app_id, info.channel, info.token, info.uid);
      await client.publish(s.mic);
      setMicLabel(s.mic.getTrackLabel?.() || "");
      const since = Date.now();
      let loudest = 0;
      s.meter = setInterval(() => {
        const mic = s.mic?.enabled === false ? 0 : s.mic?.getVolumeLevel() || 0;
        loudest = Math.max(loudest, mic);
        setMicLevel(mic);
        setVolume(s.agentTrack ? s.agentTrack.getVolumeLevel() : 0);
        // Agora can't hear a mic that never registers sound (wrong device, muted in the OS).
        setMicSilent(Date.now() - since > 8000 && loudest < 0.01);
      }, 80);
      setState("live");
    } catch (e) {
      await teardown();
      setError(friendly(e));
      setState("error");
    }
  }, [projectId, teardown]);

  const stop = useCallback(async () => {
    setState("ending");
    await teardown();
    setState("idle");
  }, [teardown]);

  const toggleMute = useCallback(async () => {
    const mic = session.current?.mic;
    if (!mic) return;
    const next = !muted;
    // setEnabled(false) and setMuted(true) both look like "user left" to Agora's agent, which then
    // stops. Sending silence keeps the call (and Vox's announcements) going; only your side is quiet.
    mic.setVolume(next ? 0 : 100);
    setMuted(next);
  }, [muted]);

  // Project change or unmount ends the call; closing the tab tells the server too.
  useEffect(() => {
    if (projectId) loadAgora().catch(() => {});
    const onUnload = () => {
      const agentId = session.current?.agentId;
      if (agentId) navigator.sendBeacon("/api/voice/stop", new Blob([JSON.stringify({ agent_id: agentId })], { type: "application/json" }));
    };
    window.addEventListener("pagehide", onUnload);
    return () => {
      window.removeEventListener("pagehide", onUnload);
      teardown();
    };
  }, [projectId, teardown]);

  return {
    state, volume, micLevel, micLabel, micSilent, muted, denoised, error, start, stop, toggleMute,
    speaking: volume > 0.05,
    userSpeaking: !muted && micLevel > 0.06,
  };
}
