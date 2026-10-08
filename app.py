from __future__ import annotations

import asyncio
import logging
import os
import random
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Literal

import httpx
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("ollama-router")

OperatingSystem = Literal["linux", "macos"]


@dataclass(frozen=True, slots=True)
class OllamaServer:
    name: str
    host: str
    port: int
    bearer_token: str
    os: OperatingSystem

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def headers(self) -> dict[str, str]:
        if not self.bearer_token:
            return {}
        return {"Authorization": f"Bearer {self.bearer_token}"}

TOKEN="ef45e6f7357cafef8a5b579d2e7bfe4ac1b341967252f37bded93ecb2ef65a71"
OLLAMA_ROUTER_BEARER_TOKEN="698229e43190406cf624c1a6e82e844b74e1629860661c46c232080522b5b8ed"

# Il campo `os` è necessario per applicare la precedenza Linux > macOS.
# Sostituisci indirizzi e token con quelli reali.
SERVERS: list[OllamaServer] = [
    OllamaServer(
        name="dgx-1",
        host="10.216.20.142",
        port=11435,
        bearer_token=TOKEN,
        os="linux",
    ),
    OllamaServer(
        name="dgx-2",
        host="10.216.20.141",
        port=11435,
        bearer_token=TOKEN,
        os="linux",
    ),
    OllamaServer(
        name="dgx-3",
        host="10.216.20.140",
        port=11435,
        bearer_token=TOKEN,
        os="linux",
    ),
    OllamaServer(
        name="mac-195",
        host="10.216.20.199",
        port=11435,
        bearer_token=TOKEN,
        os="macos",
    ),
    OllamaServer(
        name="mac-196",
        host="10.216.20.96",
        port=11435,
        bearer_token=TOKEN,
        os="macos",
    ),
    OllamaServer(
        name="mac-197",
        host="10.216.20.97",
        port=11435,
        bearer_token=TOKEN,
        os="macos",
    ),
    OllamaServer(
        name="mac-198",
        host="10.216.20.98",
        port=11435,
        bearer_token=TOKEN,
        os="macos",
    ),
]

CONNECT_TIMEOUT = 3.0
METADATA_TIMEOUT = 10.0
# None evita un timeout totale durante generazioni lunghe.
GENERATION_TIMEOUT = httpx.Timeout(connect=5.0, read=None, write=60.0, pool=5.0)

# Token che i client, incluso Open WebUI, devono usare per chiamare il wrapper.
# In produzione è preferibile impostarlo tramite variabile d'ambiente:
#   export OLLAMA_ROUTER_BEARER_TOKEN='un-token-lungo-e-casuale'
WRAPPER_BEARER_TOKEN = os.getenv(
    "OLLAMA_ROUTER_BEARER_TOKEN",
    OLLAMA_ROUTER_BEARER_TOKEN,
)


@dataclass(slots=True)
class ServerSnapshot:
    server: OllamaServer
    ps_models: list[dict[str, Any]]
    tag_models: list[dict[str, Any]]


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.metadata_client = httpx.AsyncClient(
        timeout=httpx.Timeout(METADATA_TIMEOUT, connect=CONNECT_TIMEOUT),
    )
    app.state.generation_client = httpx.AsyncClient(timeout=GENERATION_TIMEOUT)
    try:
        yield
    finally:
        await app.state.metadata_client.aclose()
        await app.state.generation_client.aclose()


app = FastAPI(
    title="Ollama RAM-aware router",
    version="1.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def require_bearer_token(request: Request, call_next):
    """Protegge tutte le route del wrapper con un Bearer token in ingresso."""
    authorization = request.headers.get("authorization", "")
    scheme, separator, supplied_token = authorization.partition(" ")

    authenticated = (
        separator == " "
        and scheme.lower() == "bearer"
        and bool(supplied_token)
        and secrets.compare_digest(supplied_token, WRAPPER_BEARER_TOKEN)
    )

    if not authenticated:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"error": "Bearer token mancante o non valido."},
            headers={"WWW-Authenticate": "Bearer"},
        )

    return await call_next(request)


def model_names(model: dict[str, Any]) -> set[str]:
    """Restituisce tutti i nomi utilizzabili presenti in una voce Ollama."""
    return {
        value
        for key in ("name", "model")
        if isinstance((value := model.get(key)), str) and value
    }


def canonical_model_name(model: dict[str, Any]) -> str | None:
    value = model.get("model") or model.get("name")
    return value if isinstance(value, str) and value else None


def model_matches(model: dict[str, Any], requested: str) -> bool:
    return requested in model_names(model)


async def fetch_json(
    client: httpx.AsyncClient,
    server: OllamaServer,
    path: str,
) -> dict[str, Any]:
    response = await client.get(
        f"{server.base_url}{path}",
        headers=server.headers,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError(f"Risposta non valida da {server.name} per {path}")
    return data


async def fetch_snapshot(server: OllamaServer, request: Request) -> ServerSnapshot | None:
    client: httpx.AsyncClient = request.app.state.metadata_client
    try:
        ps_data, tags_data = await asyncio.gather(
            fetch_json(client, server, "/api/ps"),
            fetch_json(client, server, "/api/tags"),
        )
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Server %s non disponibile: %s", server.name, exc)
        return None

    ps_models = ps_data.get("models", [])
    tag_models = tags_data.get("models", [])
    return ServerSnapshot(
        server=server,
        ps_models=ps_models if isinstance(ps_models, list) else [],
        tag_models=tag_models if isinstance(tag_models, list) else [],
    )


async def fetch_all_snapshots(request: Request) -> list[ServerSnapshot]:
    results = await asyncio.gather(*(fetch_snapshot(server, request) for server in SERVERS))
    return [snapshot for snapshot in results if snapshot is not None]


def choose_server_for_model(
    requested_model: str,
    snapshots: list[ServerSnapshot],
) -> OllamaServer:
    candidates = [
        snapshot.server
        for snapshot in snapshots
        if any(model_matches(model, requested_model) for model in snapshot.ps_models)
    ]

    if not candidates:
        raise HTTPException(
            status_code=404,
            detail=f"Il modello '{requested_model}' non è caricato in RAM su nessun server Ollama.",
        )

    linux_candidates = [server for server in candidates if server.os == "linux"]
    preferred = linux_candidates or [server for server in candidates if server.os == "macos"]
    selected = random.choice(preferred)

    logger.info(
        "Modello %s instradato verso %s (%s)",
        requested_model,
        selected.name,
        selected.os,
    )
    return selected


def deduplicate_ps(snapshots: list[ServerSnapshot]) -> list[dict[str, Any]]:
    """
    Deduplica per nome modello. Se presente su Linux e macOS, conserva la voce
    proveniente da Linux; a parità conserva la prima incontrata.
    """
    selected: dict[str, tuple[int, dict[str, Any]]] = {}

    for snapshot in snapshots:
        priority = 0 if snapshot.server.os == "linux" else 1
        for model in snapshot.ps_models:
            name = canonical_model_name(model)
            if not name:
                continue
            current = selected.get(name)
            if current is None or priority < current[0]:
                selected[name] = (priority, model)

    return [selected[name][1] for name in sorted(selected)]


def deduplicate_tags(snapshots: list[ServerSnapshot]) -> list[dict[str, Any]]:
    """Espone in /api/tags solo modelli attualmente presenti in /api/ps."""
    selected: dict[str, tuple[int, dict[str, Any]]] = {}

    for snapshot in snapshots:
        priority = 0 if snapshot.server.os == "linux" else 1
        loaded_names: set[str] = set()
        for ps_model in snapshot.ps_models:
            loaded_names.update(model_names(ps_model))

        tags_by_name: dict[str, dict[str, Any]] = {}
        for tag_model in snapshot.tag_models:
            for name in model_names(tag_model):
                tags_by_name[name] = tag_model

        for ps_model in snapshot.ps_models:
            canonical = canonical_model_name(ps_model)
            if not canonical:
                continue

            # Preferisce i metadati completi di /api/tags; se non disponibili,
            # costruisce una voce compatibile partendo da /api/ps.
            tag_model = tags_by_name.get(canonical)
            if tag_model is None:
                for alias in model_names(ps_model):
                    if alias in tags_by_name:
                        tag_model = tags_by_name[alias]
                        break

            if tag_model is None:
                tag_model = {
                    key: value
                    for key, value in ps_model.items()
                    if key not in {"expires_at", "size_vram", "context_length"}
                }

            current = selected.get(canonical)
            if current is None or priority < current[0]:
                selected[canonical] = (priority, tag_model)

    return [selected[name][1] for name in sorted(selected)]


@app.get("/api/version")
async def api_version(request: Request):
    if not SERVERS:
        raise HTTPException(status_code=503, detail="Nessun server Ollama configurato.")

    first = SERVERS[0]
    client: httpx.AsyncClient = request.app.state.metadata_client
    try:
        data = await fetch_json(client, first, "/api/version")
    except httpx.HTTPStatusError as exc:
        return JSONResponse(
            status_code=exc.response.status_code,
            content={"error": f"Errore restituito da {first.name}"},
        )
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Il primo server Ollama ({first.name}) non è raggiungibile: {exc}",
        ) from exc
    return data


@app.get("/api/ps")
async def api_ps(request: Request):
    snapshots = await fetch_all_snapshots(request)
    if not snapshots and SERVERS:
        raise HTTPException(status_code=503, detail="Nessun server Ollama raggiungibile.")
    return {"models": deduplicate_ps(snapshots)}


@app.get("/api/tags")
async def api_tags(request: Request):
    snapshots = await fetch_all_snapshots(request)
    if not snapshots and SERVERS:
        raise HTTPException(status_code=503, detail="Nessun server Ollama raggiungibile.")
    return {"models": deduplicate_tags(snapshots)}


async def proxy_generation(request: Request, upstream_path: str):
    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Il body deve essere JSON valido.") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Il body JSON deve essere un oggetto.")

    requested_model = payload.get("model")
    if not isinstance(requested_model, str) or not requested_model:
        raise HTTPException(status_code=400, detail="Il campo 'model' è obbligatorio.")

    snapshots = await fetch_all_snapshots(request)
    if not snapshots:
        raise HTTPException(status_code=503, detail="Nessun server Ollama raggiungibile.")

    server = choose_server_for_model(requested_model, snapshots)
    client: httpx.AsyncClient = request.app.state.generation_client

    upstream_request = client.build_request(
        method="POST",
        url=f"{server.base_url}{upstream_path}",
        headers={
            **server.headers,
            "Content-Type": "application/json",
            "Accept": request.headers.get("accept", "application/x-ndjson"),
        },
        json=payload,
    )

    try:
        upstream_response = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Errore di connessione al server {server.name}: {exc}",
        ) from exc

    async def body_iterator() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream_response.aiter_raw():
                if chunk:
                    yield chunk
        finally:
            await upstream_response.aclose()

    response_headers: dict[str, str] = {}
    for header in ("content-type", "cache-control"):
        if value := upstream_response.headers.get(header):
            response_headers[header] = value

    return StreamingResponse(
        body_iterator(),
        status_code=upstream_response.status_code,
        headers=response_headers,
        media_type=None,
    )


@app.post("/api/generate")
async def api_generate(request: Request):
    return await proxy_generation(request, "/api/generate")


@app.post("/api/chat")
async def api_chat(request: Request):
    return await proxy_generation(request, "/api/chat")


@app.get("/health")
async def health(request: Request):
    snapshots = await fetch_all_snapshots(request)
    reachable = {snapshot.server.name for snapshot in snapshots}
    return {
        "status": "ok" if reachable else "degraded",
        "servers": [
            {
                "name": server.name,
                "os": server.os,
                "url": server.base_url,
                "reachable": server.name in reachable,
            }
            for server in SERVERS
        ],
    }
