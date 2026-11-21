import { useEffect, useRef } from "react";

const BRIEF_LABEL = {
  business_name: "Business", what_it_does: "What it does", target_audience: "Customers", goal: "Goal",
  theme: "Theme", colours: "Colours", must_haves: "Must-haves", tone: "Tone", location: "Location",
  contact: "Contact", notes: "Notes",
};

const TIME = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });

export default function ActivityLog({ entries, tierLabel, isBlank, mode, onAction }) {
  const endRef = useRef(null);
  // Braces matter: newer browsers return a Promise from scrollIntoView, which React would treat as a cleanup.
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "nearest" });
  }, [entries.length]);

  if (entries.length === 0) {
    return (
      <div className="feed-empty">
        <p className="feed-empty-title">{isBlank ? "Start with a description" : "No changes yet in this session"}</p>
        <p>
          {isBlank
            ? mode === "oneshot"
              ? "One-shot: say or type the whole idea in one sentence. The researcher works out the palette, fonts, sections and photos, and the team builds it straight away."
              : "Business: Vox asks about your business, customers, goal and style, one question at a time. Your answers become a brief, the researcher plans from it, and once you approve the plan the team builds it."
            : "Ask for a change by voice or type it below. Each request and its result appears here."}
        </p>
      </div>
    );
  }

  return (
    <ol className="feed">
      {entries.map((e) => (
        <li key={e.id} className={`entry is-${e.kind}`}>
          <div className="entry-head">
            <span className="entry-who">{e.who || (e.kind === "user" ? "You" : e.kind === "research" ? "Researcher" : "Vox")}</span>
            {e.tier && e.kind !== "task" && <span className="entry-tier">{tierLabel[e.tier] || e.tier}</span>}
            <time className="entry-time">{TIME.format(e.ts)}</time>
          </div>
          <p className="entry-text">{e.text}</p>
          {e.fields && (
            <dl className="brief">
              {Object.entries(e.fields).map(([k, v]) => (
                <div key={k}>
                  <dt>{BRIEF_LABEL[k] || k}</dt>
                  <dd>{v}</dd>
                </div>
              ))}
            </dl>
          )}
          {e.fonts?.heading && (
            <div className="specimen">
              {e.fonts.link && <link rel="stylesheet" href={e.fonts.link} />}
              <span style={{ fontFamily: e.fonts.css?.heading }}>{e.fonts.heading}</span>
              <span style={{ fontFamily: e.fonts.css?.body }}>{e.fonts.body}</span>
            </div>
          )}
          {e.palette && (
            <div className="swatches" aria-label="Researched palette">
              {Object.entries(e.palette).map(([name, color]) => (
                <span key={name} className="swatch" style={{ background: color }} title={`${name} ${color}`} />
              ))}
            </div>
          )}
          {e.actions?.length > 0 && (
            <div className="entry-actions">
              {e.resolved ? (
                <span className="entry-resolved">{e.resolved}</span>
              ) : (
                e.actions.map((action) => (
                  <button key={action.label} className={`btn small ${action.primary ? "primary" : ""}`} onClick={() => onAction(e.id, action)}>
                    {action.label}
                  </button>
                ))
              )}
            </div>
          )}
          {e.images?.length > 0 && (
            <div className="thumbs">
              {e.images.map((img) => (
                <img key={img.path} src={img.src} alt={img.alt} title={img.alt} loading="lazy" />
              ))}
            </div>
          )}
        </li>
      ))}
      <li ref={endRef} aria-hidden="true" className="feed-end" />
    </ol>
  );
}
