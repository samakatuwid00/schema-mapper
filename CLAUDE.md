## Project quick reference

- Spec-driven changes live in `openspec/` — read
  `openspec/changes/add-admin-database-dashboard/{proposal,design,tasks}.md` before
  touching the admin UI; validate with `openspec validate <change> --strict`.
- Admin API: `python -m src.admin_api.app` (FastAPI, port 8400; needs
  `ADMIN_SESSION_SECRET` in .env). Frontend: `cd web && npm run dev` (proxies /api).
- Web/API both call `src/services/` — never bypass it to reimplement workflow logic,
  and never accept a client-supplied actor; identity comes from the session.
- Tests: `pytest -q`. Full stack: `docker compose up -d` then see README "Admin web UI".

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
