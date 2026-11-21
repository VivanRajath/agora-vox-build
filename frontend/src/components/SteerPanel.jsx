import { useState } from "react";
import { CloseIcon } from "./icons.jsx";

const SWITCHES = [
  ["ask_first", "Ask before building", "Vox reads back what it heard and waits for your yes."],
  ["suggestions", "Offer suggestions", "Off: Vox does exactly what you say, no ideas of its own."],
  ["short", "Short replies", "One sentence at a time."],
];

// You direct, the agents follow: standing rules go to every agent (Vox, researcher, developers,
// design critic) and outrank their own taste. Vox can also set these by voice ("from now on...").
export default function SteerPanel({ steering, onChange }) {
  const [draft, setDraft] = useState("");
  const s = steering || { rules: [], ask_first: true, suggestions: false, short: true };

  const addRule = (e) => {
    e.preventDefault();
    const rule = draft.trim();
    if (!rule) return;
    onChange({ ...s, rules: [...s.rules, rule] });
    setDraft("");
  };

  return (
    <div className="steer">
      <p className="steer-lead">You direct, the agents follow. Rules here go to Vox, the researcher, the developers and the design critic, and beat their own taste.</p>

      <form className="steer-add" onSubmit={addRule}>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="e.g. Always use a dark theme"
          aria-label="New rule for every agent"
        />
        <button className="btn small primary" type="submit" disabled={!draft.trim()}>Add rule</button>
      </form>

      {s.rules.length > 0 ? (
        <ul className="steer-rules">
          {s.rules.map((rule) => (
            <li key={rule}>
              <span>{rule}</span>
              <button className="icon-btn small" aria-label={`Remove rule: ${rule}`} onClick={() => onChange({ ...s, rules: s.rules.filter((r) => r !== rule) })}>
                <CloseIcon />
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted-text">No rules yet. Say "from now on, never use photos of people" or type one above.</p>
      )}

      <div className="steer-switches">
        {SWITCHES.map(([key, label, hint]) => (
          <label key={key} className="steer-switch">
            <input type="checkbox" checked={!!s[key]} onChange={(e) => onChange({ ...s, [key]: e.target.checked })} />
            <span>
              <strong>{label}</strong>
              <em>{hint}</em>
            </span>
          </label>
        ))}
      </div>
    </div>
  );
}
