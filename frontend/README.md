# ICF SPARK Research Concierge — branded frontend (M7.1)

This is the implemented React/Vite frontend for the existing SPARK MedGemma research concierge. It retains the M7 backend contract and the original-only dbGaP metadata search; there are **no new backend routes, database migrations, or model changes**.

## Functional screens

- ICF-branded research chat, with the ICF mark at top left, a soft blue hero, and source-backed study results.
- Real `POST /api/v1/chat` MedGemma messages and search results, with chronological chat history stored in the browser.
- Real `GET /api/v1/studies/{study_id}` study details and original phenotype/genotype dataset tabs.
- Save/remove studies and reopen previous chats (browser `localStorage`, not a shared server database).
- Study discovery history, starter use cases, and a help screen.
- Responsive mobile navigation and a details drawer for small screens.

No example result cards, study counts, participant numbers, access URLs, or medical concept mappings are fabricated. The application labels original-source search matches as unverified.

## Build/rebuild

From the repository root **after M7 has already been installed**:

```bash
docker compose -f compose.yaml -f compose.chat.yaml --profile ai up -d --build --no-deps chat-ui
```

If you normally use GPU overlay for Ollama, add `-f compose.gpu.yaml` before `--profile ai`. This command rebuilds only the frontend; it preserves existing containers and volumes.

Open <http://127.0.0.1:18081> or the configured `CHAT_PORT`.

The Dockerfile uses `npm install` because the preexisting M7 baseline has no lockfile. The source and direct dependencies are pinned. For reproducible production packaging, create and commit `frontend/package-lock.json`, switch to `npm ci`, and retest. Node/npm dependencies could not be fetched in the isolated patch-authoring environment; the application needs an npm-enabled Docker build on your machine.

## Local development and safety

The nginx configuration proxies `/api/` to the existing FastAPI service. Model access stays server-side. The interface has no login, shared storage, or production rate limiting; do not expose this prototype directly to the public internet. Browser-local chat history may contain research questions, so avoid using it for sensitive subject information on shared machines. Browser storage can be cleared using site-data settings.

The ICF PNG mark is a cropped/cleaned asset from the ICF screenshot provided for the design reference. Replace it with an official ICF SVG asset for production when branding assets are available.
