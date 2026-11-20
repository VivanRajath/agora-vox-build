# Hard Rules

## Always
- Record every change as a new version so it can be undone.
- Build every version (import whitelist, esbuild, Tailwind) before committing it.
- Escalate to the next tier, with the error, when a patch cannot be applied or does not build.
- Keep component names, section ids, data-section and data-vox-id attributes stable across edits.
- Reply to the voice agent with one short, speakable sentence.
- Plan a new site and wait for the user's approval before building it.
- Publish only after the user approved that exact repository (enforced in publish_site).

## Never
- Expose Agora, Murf or LLM keys to the browser.
- Accept /llm calls without a valid per-session token signed with TOOL_SECRET.
- Write outside a project's site directory.
- Run generated site code on the server. Sites are only bundled and served as static files.
- Let generated code import anything but react, react-dom, lucide-react, framer-motion and its own files.

## Boundaries

### code-editor (edit orchestrator)
- Delegates to sub-agents by tier. Does not edit directly.
- **jnr-developer**: small, local patches to the relevant component or data file.
- **snr-developer**: feature-sized multi-file patches (new components, App.jsx, site.js); build fixes.
- **architect**: new sites and full rewrites of every file; the final fallback.
