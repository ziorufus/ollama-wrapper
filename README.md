# Ollama RAM-aware router

## Configurazione

Modificare `SERVERS` in `app.py` con indirizzi e sistemi operativi dei backend Ollama.

I token non sono nel codice: vengono letti da variabili d'ambiente, normalmente tramite un file `.env` (escluso da git). Partire dal modello:

```bash
cp .env.example .env
chmod 600 .env
```

e compilare le due variabili:

- `OLLAMA_UPSTREAM_BEARER_TOKEN`: token usato dal wrapper per autenticarsi verso i backend Ollama (assegnato di default a tutti i server in `SERVERS`).
- `OLLAMA_ROUTER_BEARER_TOKEN`: token che i client (es. Open WebUI) devono presentare per chiamare il wrapper. Deve essere distinto da quello dei backend.

Per generare un token:

```bash
openssl rand -hex 32
```

Se una delle due variabili manca, il servizio non si avvia.

## Avvio con Docker (consigliato)

```bash
docker compose up -d --build
```

Il container si chiama `ollama-wrapper`, espone la porta `11434` e legge i token da `.env`. Grazie a `restart: unless-stopped` riparte automaticamente al riavvio della macchina (purché il servizio `docker` sia abilitato: `sudo systemctl enable docker`).

Comandi utili:

```bash
docker compose logs -f          # log
docker compose up -d --build    # dopo una modifica ad app.py
docker compose up -d            # dopo una modifica a .env
docker compose down             # arresto
```

## Avvio manuale

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 11434 --env-file .env
```

## Configurazione di Open WebUI

Configurare una sola connessione Ollama, puntata al wrapper, e impostare lo stesso token come Bearer/API key:

```text
http://IP-DEL-WRAPPER:11434
```

Il wrapper verifica il token ricevuto da Open WebUI e non lo inoltra ai backend. Per ogni backend usa invece il rispettivo `bearer_token` configurato in `SERVERS` (di default `OLLAMA_UPSTREAM_BEARER_TOKEN`).

## Routing

- Sono accettati solo modelli restituiti da `/api/ps` di almeno un backend.
- Se il modello è caricato su uno o più Linux, viene scelto casualmente un Linux.
- Altrimenti viene scelto casualmente uno dei macOS che lo hanno caricato.
- `/api/tags` e `/api/ps` espongono solo modelli caricati, deduplicati per nome.
- `/api/version` viene inoltrato al primo server configurato.
- Tutte le route, compresa `/health`, richiedono autenticazione Bearer.

## Verifica

```bash
# Il valore di OLLAMA_ROUTER_BEARER_TOKEN in .env
TOKEN='INSERISCI_IL_TOKEN_DEL_WRAPPER'

curl http://127.0.0.1:11434/api/ps \
  -H "Authorization: Bearer $TOKEN"

curl http://127.0.0.1:11434/api/tags \
  -H "Authorization: Bearer $TOKEN"

curl http://127.0.0.1:11434/api/version \
  -H "Authorization: Bearer $TOKEN"

curl http://127.0.0.1:11434/api/generate \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3:8b","prompt":"Ciao","stream":true}'
```

Senza token, oppure con token errato, il wrapper risponde con `401 Unauthorized` e l'header `WWW-Authenticate: Bearer`.
