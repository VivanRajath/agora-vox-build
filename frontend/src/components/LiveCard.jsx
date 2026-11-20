import { useEffect, useState } from "react";
import QRCode from "qrcode";
import { ExternalIcon } from "./icons.jsx";

const PROGRESS = {
  pushing: "Uploading the site to GitHub…",
  deploying: "GitHub Pages is building it, usually under a minute…",
  checking: "Almost live, checking the address…",
};

// Shown once a publish starts: progress, then the live address and a QR code.
export default function LiveCard({ publish, published, current }) {
  const [qr, setQr] = useState(null);
  const url = published?.url;

  useEffect(() => {
    if (!url) return;
    let cancelled = false;
    QRCode.toDataURL(url, { margin: 1, width: 360, errorCorrectionLevel: "M" })
      .then((data) => !cancelled && setQr(data))
      .catch(() => setQr(null));
    return () => {
      cancelled = true;
    };
  }, [url]);

  const inProgress = PROGRESS[publish?.state];
  if (inProgress) {
    return (
      <section className="live is-progress" aria-live="polite">
        <span className="progress" aria-hidden="true" />
        <p>{inProgress}</p>
      </section>
    );
  }
  if (!url) return null;

  const stale = published.version !== current;
  return (
    <section className="live" aria-label="Your live website">
      <div className="live-text">
        <span className="live-badge">Live</span>
        <a className="live-url" href={url} target="_blank" rel="noreferrer">
          {url.replace(/^https?:\/\//, "").replace(/\/$/, "")} <ExternalIcon />
        </a>
        <p className="live-hint">
          {stale ? `Shows version ${published.version}. Say "republish" to update it.` : "Scan to open it on a phone, or print it for your shop counter."}
        </p>
      </div>
      {qr && <img className="live-qr" src={qr} alt={`QR code for ${url}`} width="120" height="120" />}
    </section>
  );
}
