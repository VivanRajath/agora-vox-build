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
  const [muted, setMuted] = useState(false);
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
      s.meter = setInterval(() => setVolume(s.agentTrack ? s.agentTrack.getVolumeLevel() : 0), 100);
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
    await mic.setEnabled(!next);
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

  return { state, volume, muted, error, start, stop, toggleMute, speaking: volume > 0.05 };
}
