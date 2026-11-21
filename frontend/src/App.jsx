import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { useVoiceCall } from "./useVoiceCall.js";
import Preview from "./components/Preview.jsx";
import VoicePanel from "./components/VoicePanel.jsx";
import ActivityLog from "./components/ActivityLog.jsx";
import History from "./components/History.jsx";
import AccountMenu from "./components/AccountMenu.jsx";
import SettingsModal from "./components/SettingsModal.jsx";
import GitHubModal from "./components/GitHubModal.jsx";
import Composer from "./components/Composer.jsx";
import LiveCard from "./components/LiveCard.jsx";
import { DownloadIcon, GitHubIcon, LinkIcon, Logo, SettingsIcon } from "./components/icons.jsx";

const TIER_LABEL = {
  research: "Researcher",
  team: "Font Knight and Copywriter",
  architect: "Architect",
  critic: "Design critic",
  jnr: "Junior dev",
  snr: "Senior dev",
  repair: "Senior dev (build fix)",
};
const AGENT_LABEL = { fontknight: "Font Knight", copywriter: "Copywriter", critic: "Design critic" };
const TOOL_LABEL = {
  plan_site: "Planning",
  build_site: "Building the site",
  publish_site: "Publishing",
  publication_status: "Checking the live site",
  edit_site: "Editing the site",
  undo_change: "Undoing",
  describe_site: "Reading the page",
  check_progress: "Checking progress",
  build: "Build",
};
const TOOL_STATUS = {
  started: "started",
  done: "done",
  in_progress: "running in the background",
  failed: "failed",
  needs_approval: "waiting for your approval",
  name_taken: "name taken",
  needs_github: "needs GitHub",
};
const repoName = (full) => (full || "").split("/").pop();

function useProjectId() {
  const [projectId, setProjectId] = useState(() => new URLSearchParams(location.search).get("p"));
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    const ensure = async () => {
      if (projectId) {
        try {
          await api.getProject(projectId);
          return;
        } catch {
          /* stale link: fall through and start a fresh project */
        }
      }
      try {
        const meta = await api.createProject();
        if (cancelled) return;
        history.replaceState(null, "", `?p=${meta.id}`);
        setProjectId(meta.id);
      } catch (e) {
        setError(e.message);
      }
    };
    ensure();
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  return { projectId, error };
}

export default function App() {
  const { projectId, error: projectError } = useProjectId();
  const voice = useVoiceCall(projectId);
  const [heard, setHeard] = useState(""); // what Vox last heard, for the orb's caption
  const [thinking, setThinking] = useState(false); // a user turn reached Vox and no reply yet
  useEffect(() => {
    if (voice.speaking) setThinking(false);
  }, [voice.speaking]);
  useEffect(() => {
    if (voice.state === "idle") {
      setHeard("");
      setThinking(false);
    }
  }, [voice.state]);
  const [config, setConfig] = useState(null);
  const [meta, setMeta] = useState(null);
  const [log, setLog] = useState([]);
  const [busy, setBusy] = useState(null);
  const [selection, setSelection] = useState(null);
  const [picking, setPicking] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [user, setUser] = useState(null);
  const [aiReady, setAiReady] = useState(true);
  const [dialog, setDialog] = useState(null); // "settings" | "github"
  const [tab, setTab] = useState("activity");
  const [copied, setCopied] = useState(false);
  const [githubNeeded, setGithubNeeded] = useState(false);
  const nextId = useRef(1);

  const addLog = useCallback((entry) => {
    setLog((prev) => [...prev.slice(-80), { id: nextId.current++, ts: Date.now(), ...entry }]);
  }, []);

  // Buttons on an activity entry (build the plan, approve publishing, pick a repo name). One click resolves it.
  const runAction = useCallback(async (entryId, action) => {
    setLog((prev) => prev.map((e) => (e.id === entryId ? { ...e, resolved: action.label } : e)));
    try {
      await action.run();
    } catch (err) {
      addLog({ kind: "error", text: err.message });
    }
  }, [addLog]);

  useEffect(() => {
    api.config().then(setConfig).catch(() => setConfig({ voice_enabled: false, llm_provider: "none" }));
    api.me().then((r) => setUser(r.user)).catch(() => {});
  }, []);

  useEffect(() => {
    if (!projectId) return;
    // After the project exists, so this browser's guest account is already in the session cookie.
    api.settings().then(applySettings).catch(() => {});
    api.getProject(projectId).then((m) => {
      setMeta(m);
      setSelection(m.selection);
    });

    const source = new EventSource(`/api/projects/${projectId}/events`);
    source.onmessage = (msg) => {
      const ev = JSON.parse(msg.data);
      switch (ev.type) {
        case "job_started":
          setBusy({ instruction: ev.instruction, tier: ev.tier });
          addLog({ kind: "task", who: "Task", text: ev.instruction, tier: ev.tier });
          break;
        case "job_stage":
          setBusy((b) => (b ? { ...b, tier: ev.tier } : b));
          break;
        case "research_done":
          addLog({
            kind: "research",
            text: ev.summary,
            palette: ev.palette,
            images: (ev.images || []).map((img) => ({ ...img, src: `/preview/${projectId}/${img.path}` })),
          });
          break;
        case "agent_note":
          addLog({ kind: "research", who: AGENT_LABEL[ev.agent] || ev.agent, text: ev.summary, fonts: ev.fonts });
          break;
        case "job_escalated":
          setBusy((b) => (b ? { ...b, tier: ev.tier } : b));
          addLog({ kind: "info", text: `Handing over to the ${TIER_LABEL[ev.tier] || ev.tier}` });
          break;
        case "job_done":
          setBusy(null);
          setMeta((m) => ({ ...m, current: ev.current, versions: ev.versions }));
          setReloadKey((k) => k + 1);
          addLog({ kind: "done", text: ev.version.summary, tier: ev.version.tier });
          break;
        case "job_failed":
          setBusy(null);
          addLog({ kind: "error", text: ev.reason });
          break;
        case "job_planned":
          setBusy(null);
          break;
        case "plan":
          setMeta((m) => ({ ...m, draft: { spoken: ev.spoken } }));
          addLog({
            kind: "plan",
            who: "Plan",
            text: ev.spoken,
            palette: ev.brief?.palette,
            actions: [{ label: "Build it", primary: true, run: () => api.command(projectId, "build it") }],
          });
          break;
        case "approval":
          if (ev.state === "needs_approval") {
            addLog({
              kind: "approval",
              who: "Needs your OK",
              text: ev.message,
              actions: [
                { label: "Publish", primary: true, run: () => api.publish(projectId, { repo_name: repoName(ev.repo), confirmed: true }) },
                { label: "Not now", run: async () => {} },
              ],
            });
          } else if (ev.state === "name_taken") {
            addLog({
              kind: "approval",
              who: "Name taken",
              text: `You already have ${ev.repo}. Pick another name:`,
              actions: (ev.options || []).map((name) => ({ label: name, run: () => api.publish(projectId, { repo_name: name, confirmed: false }) })),
            });
          }
          break;
        case "needs_github":
          setGithubNeeded(true);
          addLog({ kind: "error", who: "Publishing", text: "Connect GitHub to put the site online, or download the ZIP." });
          break;
        case "publish":
          setMeta((m) => ({ ...m, publish: ev }));
          if (ev.state === "failed") addLog({ kind: "error", who: "Publishing", text: ev.reason });
          break;
        case "published":
          setGithubNeeded(false);
          setMeta((m) => ({ ...m, published: { url: ev.url, repo: ev.repo, version: ev.version }, publish: { state: "live" } }));
          addLog({ kind: "done", who: "Live", text: `Your site is live at ${ev.url}` });
          break;
        case "version":
          setMeta((m) => ({ ...m, current: ev.current, versions: ev.versions }));
          setReloadKey((k) => k + 1);
          if (ev.undo) addLog({ kind: "info", text: "Undid the last change" });
          break;
        case "selection":
          setSelection(ev.selection);
          break;
        case "transcript":
          addLog({ kind: ev.role === "user" ? "user" : "vox", text: ev.text });
          if (ev.role === "user") {
            setHeard(ev.text);
            setThinking(true);
          } else {
            setThinking(false);
          }
          break;
        case "tool":
          addLog({
            kind: ev.status === "failed" ? "error" : "tool",
            who: TOOL_LABEL[ev.name] || ev.name,
            text: ev.status === "started" ? "Started" : `${TOOL_STATUS[ev.status] || ev.status}${ev.message ? `: ${ev.message}` : ""}`,
          });
          break;
      }
    };
    return () => source.close();
  }, [projectId, addLog]);

  function applySettings(view) {
    setAiReady(view.keys.length > 0 || Object.keys(view.server.providers).length > 0);
    setUser(view.user);
  }

  const handleSelect = useCallback(
    (sel) => {
      setSelection(sel);
      setPicking(false);
      api.select(projectId, sel).catch(() => {});
    },
    [projectId]
  );

  const clearSelection = () => {
    setSelection(null);
    api.select(projectId, null).catch(() => {});
  };

  const sendCommand = async (text) => {
    try {
      await api.command(projectId, text);
    } catch (e) {
      addLog({ kind: "error", text: e.message });
    }
  };

  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(location.href);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      /* clipboard blocked: nothing to confirm */
    }
  };

  if (projectError) {
    return (
      <div className="fatal">
        <p><strong>Can't reach the Vox Build server.</strong></p>
        <p>{projectError}. Check that the backend is running, then reload the page.</p>
      </div>
    );
  }
  if (!projectId || !meta) return <div className="fatal" aria-busy="true"><span className="progress" /></div>;

  const isBlank = meta.current === 0 && meta.versions.length === 1;
  const latest = meta.versions.length ? meta.versions[meta.versions.length - 1].n : 0;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <Logo />
          <span className="brand-name">Vox Build</span>
          <span className="brand-sep" aria-hidden="true">/</span>
          <span className="project-name" title={meta.brief?.business || "Untitled site"}>{meta.brief?.business || "Untitled site"}</span>
        </div>
        <div className="topbar-actions">
          <button className="btn quiet hide-sm" onClick={copyLink}>
            <LinkIcon /> {copied ? "Link copied" : "Copy link"}
          </button>
          <a className="btn quiet" href={api.downloadUrl(projectId)} download title="Download the site as a ZIP">
            <DownloadIcon /> <span className="hide-sm">Download ZIP</span>
          </a>
          <button
            className={`btn quiet ${githubNeeded ? "is-attention" : ""}`}
            onClick={() => (githubNeeded && !user?.github && config?.auth?.github ? (location.href = api.loginUrl("github")) : setDialog("github"))}
            title="Connect GitHub or push the source"
          >
            <GitHubIcon /> <span className="hide-sm">{githubNeeded && !user?.github ? "Connect GitHub" : "GitHub"}</span>
          </button>
          <button
            className="btn primary"
            disabled={isBlank}
            onClick={() => api.publish(projectId, { confirmed: false }).catch((e) => addLog({ kind: "error", text: e.message }))}
          >
            <span>{meta.published?.url ? "Republish" : "Put it online"}</span>
          </button>
          <button className="btn quiet" onClick={() => setDialog("settings")} title="API keys, models and photos">
            <SettingsIcon /> <span className="hide-sm">Settings</span>
          </button>
          <AccountMenu
            user={user}
            auth={config?.auth}
            onOpenSettings={() => setDialog("settings")}
            onSignedOut={() => setUser(null)}
          />
        </div>
      </header>

      {dialog === "settings" && <SettingsModal onClose={() => setDialog(null)} onChanged={applySettings} />}
      {dialog === "github" && (
        <GitHubModal
          projectId={projectId}
          meta={meta}
          user={user}
          auth={config?.auth}
          onClose={() => setDialog(null)}
          onPushed={(res) => {
            setMeta((m) => ({ ...m, github: { repo: res.repo, url: res.url } }));
            addLog({ kind: "done", text: `Pushed version ${meta.current} to ${res.repo} on GitHub.` });
          }}
        />
      )}

      <main className="workspace">
        <Preview
          projectId={projectId}
          version={meta.current}
          latest={latest}
          reloadKey={reloadKey}
          busy={busy}
          tierLabel={TIER_LABEL}
          picking={picking}
          selection={selection}
          onSelect={handleSelect}
        />

        <aside className="sidebar">
          <VoicePanel
            voice={voice}
            heard={heard}
            thinking={thinking}
            config={config}
            aiReady={aiReady}
            onOpenSettings={() => setDialog("settings")}
            picking={picking}
            onTogglePick={() => setPicking((p) => !p)}
            selection={selection}
            onClearSelection={clearSelection}
            isBlank={isBlank}
          />

          <LiveCard publish={meta.publish} published={meta.published} current={meta.current} />

          <div className="tabs" role="tablist" aria-label="Sidebar">
            {[["activity", "Activity"], ["versions", `Versions (${meta.versions.length})`]].map(([id, label]) => (
              <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? "is-on" : ""} onClick={() => setTab(id)}>
                {label}
              </button>
            ))}
          </div>
          <div className="tab-panel" role="tabpanel">
            {tab === "activity" ? (
              <ActivityLog entries={log} tierLabel={TIER_LABEL} isBlank={isBlank} onAction={runAction} />
            ) : (
              <History
                versions={meta.versions}
                current={meta.current}
                tierLabel={TIER_LABEL}
                onUndo={() => api.undo(projectId)}
                onRestore={(n) => api.restore(projectId, n)}
              />
            )}
          </div>

          <Composer busy={busy} isBlank={isBlank} onCommand={sendCommand} />
        </aside>
      </main>
    </div>
  );
}
