<!-- GLOBAL-AI-AGENTS:START -->
# Global AI Agent Knowledge & Orchestration Index

CRITICAL INSTRUCTION FOR FREEBUFF / CODEBUFF DESKTOP AND CLI

This workspace is connected to the central AI agent directory:

- **Project path:** `.agents/`  (junction created by Initialize-AgentWorkspace)
- **User path:** `%USERPROFILE%\.agents`  (`~/.agents`)

Desktop apps such as **Freebuff** and **Codebuff** do not read the user-level folder
when a project is opened via Open Folder. They DO read files in the project root.
That is why this file exists: it points you at `.agents/`.

## Mandatory pre-execution steps

Before planning, coding, refactoring, UI/UX, SEO, or security work, silently:

1. Read and enforce `.agents/rules/00-auto-orchestrator.md`
2. Read and enforce `.agents/rules/security-architect.md`
3. Scan `.agents/skills/` and load the most relevant `SKILL.md` playbooks
4. Adopt a specialist persona from `.agents/agents/` when the domain is specific
5. Use MCP servers from `.agents/mcp.json`
6. Honor slash-command playbooks in `.agents/commands/`:
   `/CyrusAsha_Auto` `/CyrusAsha_Skills` `/CyrusAsha_Rules` `/CyrusAsha_Agents` `/CyrusAsha_Hooks` `/CyrusAsha_Commands` `/CyrusAsha_Audit-Security` `/CyrusAsha_PreLaunch-Security` `/CyrusAsha_Compact` `/CyrusAsha_Compress` 
7. Read and synchronize project domain context and conventions from `knowledge.md` (and `AGENTS.md`).

If `.agents/` is missing, run in PowerShell from the project root:

    Initialize-AgentWorkspace
    # aliases: agents-init    ai-init    init-agents    init-needed-files

Do not ignore this index. Treat `.agents/` as the single source of truth.
<!-- GLOBAL-AI-AGENTS:END -->
# 🤖 Project Operating Guide: CyrusAsha_GuiForOpenDataLoaderPDF

Windows desktop control center for **OpenDataLoader PDF** — install, convert, preview and manage from one place.
Bilingual UI (فارسی / English), Python 3.9+, **standard library only**. Product version lives in `src/cyrusasha_odl/__init__.py`.

## 1. Project Architecture & Components
- `src/cyrusasha_odl/app.py`: Tkinter GUI — pages, layout and i18n wiring.
- `server.py`, `jobs.py`, `procs.py`: local backend, job queue and subprocess control of the upstream converter.
- `storage.py`, `config.py`, `env.py`: data/install folders, settings and environment detection.
- `rtl.py`, `rules.py`, `default-rules.json`, `hybrid.py`: Persian/RTL output repair, rule engine and hybrid (AI) mode.
- `src/cyrusasha_odl/data/rtl-words.txt.gz`: packaged lexicon — must stay committed (the root `data/` folder is the runtime folder and is gitignored).
- `Start.bat`, `windows/*.bat`, `windows/install-shortcut.ps1`: Windows launchers (CRLF + ASCII, enforced by tests).
- `scripts/build-release.py`: builds `release/*.zip` and its `.sha256`; `test/`: unittest suite.

## 2. Agent Operating Guidelines
- **Zero runtime dependencies**: Python standard library only; do not add third-party runtime packages.
- **Bilingual UI**: every user-facing string needs both Persian and English, and must stay RTL-safe.
- **Ignored paths**: `.agents/`, `data/`, `release/`, `.freebuff/` are gitignored — never commit them.
- **Never edit `.agents/`**: it is an NTFS junction to `%USERPROFILE%\.agents`.
- **Versioning**: bump `__version__` in `src/cyrusasha_odl/__init__.py`; launchers and docs must agree.

## 3. Verification & Quality Gates
- Tests: `python -m unittest discover -s test`
- Release bundle: `python scripts/build-release.py` (never ship `data/`, `release/` or `.freebuff/`)
- Launchers must remain CRLF and ASCII — covered by `test/test_launchers.py`.
