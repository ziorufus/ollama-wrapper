# Ollama RAM-aware router

A small FastAPI service that looks like a regular Ollama server to its clients, but spreads requests across a group of real Ollama servers.

The idea is to use only models that are **already loaded in memory** on some machine, so that no time is lost loading them: on every request the wrapper queries `/api/ps` on all backends, finds the ones that have the requested model in RAM and forwards the request there, preferring Linux machines over Macs. Streaming responses are passed through to the client as they arrive.

This way, clients such as Open WebUI can be configured with **a single connection** and see a unified list of the models available across the whole group of servers. The wrapper is protected by its own Bearer token, separate from the ones used towards the backends.

Exposed endpoints: `/api/chat`, `/api/generate`, `/api/tags`, `/api/ps`, `/api/version` and `/health` (reachability status of each backend). Details on how a server is chosen are in the [Routing](#routing) section.

## Configuration

Edit `SERVERS` in `app.py` with the addresses and operating systems of the Ollama backends.

Tokens are not stored in the code: they are read from environment variables, usually through a `.env` file (excluded from git). Start from the template:

```bash
cp .env.example .env
chmod 600 .env
```

and fill in the two variables:

- `OLLAMA_UPSTREAM_BEARER_TOKEN`: token the wrapper uses to authenticate with the Ollama backends (assigned by default to every server in `SERVERS`).
- `OLLAMA_ROUTER_BEARER_TOKEN`: token clients (e.g. Open WebUI) must present to call the wrapper. It must be different from the backend token.

To generate a token:

```bash
openssl rand -hex 32
```

If either variable is missing, the service will not start.

## Running with Docker (recommended)

```bash
docker compose up -d --build
```

The container is named `ollama-wrapper`, exposes port `11434` and reads the tokens from `.env`. Thanks to `restart: unless-stopped` it comes back up automatically when the machine reboots (as long as the `docker` service is enabled: `sudo systemctl enable docker`).

Useful commands:

```bash
docker compose logs -f          # logs
docker compose up -d --build    # after changing app.py
docker compose up -d            # after changing .env
docker compose down             # stop
```

## Running manually

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 11434 --env-file .env
```

## Open WebUI configuration

Configure a single Ollama connection pointing to the wrapper, and set the same token as the Bearer/API key:

```text
http://WRAPPER-IP:11434
```

The wrapper checks the token received from Open WebUI and does not forward it to the backends. For each backend it uses the corresponding `bearer_token` configured in `SERVERS` instead (by default `OLLAMA_UPSTREAM_BEARER_TOKEN`).

## Routing

- Only models returned by `/api/ps` on at least one backend are accepted.
- If the model is loaded on one or more Linux servers, one of them is chosen at random.
- Otherwise, one of the macOS servers that have it loaded is chosen at random.
- `/api/tags` and `/api/ps` only expose loaded models, deduplicated by name.
- `/api/version` is forwarded to the first configured server.
- All routes, including `/health`, require Bearer authentication.

## Testing

```bash
# The value of OLLAMA_ROUTER_BEARER_TOKEN in .env
TOKEN='INSERT_THE_WRAPPER_TOKEN'

curl http://127.0.0.1:11434/api/ps \
  -H "Authorization: Bearer $TOKEN"

curl http://127.0.0.1:11434/api/tags \
  -H "Authorization: Bearer $TOKEN"

curl http://127.0.0.1:11434/api/version \
  -H "Authorization: Bearer $TOKEN"

curl http://127.0.0.1:11434/api/generate \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3:8b","prompt":"Hello","stream":true}'
```

Without a token, or with a wrong one, the wrapper responds with `401 Unauthorized` and the `WWW-Authenticate: Bearer` header.
