// The live call at a glance: it grows with your voice, spins while Vox thinks,
// and pulses with Vox's voice. The caption says what Vox last heard.

function orbState(voice, thinking) {
  if (voice.state === "connecting") return ["connecting", "Connecting…"];
  if (voice.muted) return ["muted", "Microphone muted"];
  if (voice.speaking) return ["vox", "Vox is speaking"];
  if (voice.userSpeaking) return ["user", "Hearing you…"];
  if (thinking) return ["thinking", "Thinking…"];
  return ["listening", "Listening"];
}

export default function VoiceOrb({ voice, heard, thinking }) {
  const [kind, label] = orbState(voice, thinking);
  const level = kind === "vox" ? voice.volume : kind === "user" ? voice.micLevel : 0;
  const scale = 1 + Math.min(level || 0, 1) * 0.55;

  return (
    <div className={`orb-wrap is-${kind}`} role="status" aria-live="polite">
      <div className="orb-stage" aria-hidden="true">
        <span className="orb-halo" style={{ transform: `scale(${1 + (scale - 1) * 1.6})` }} />
        <span className="orb-ring" />
        <span className="orb" style={{ transform: `scale(${scale})` }} />
      </div>
      <p className="orb-label">{label}</p>
      <p className="orb-heard">
        {heard ? <>Heard: <q>{heard}</q></> : "Say something, and what Vox hears shows here."}
      </p>
      {voice.micSilent && !voice.muted && (
        <p className="notice is-error">
          Your microphone isn't picking up any sound{voice.micLabel ? ` (${voice.micLabel})` : ""}. Check it's the right
          input in your browser's site settings and in Windows sound settings, then start the call again.
        </p>
      )}
    </div>
  );
}
