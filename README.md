# Ollama RAM-aware router

## Installazione

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Modificare `SERVERS` in `app.py` con indirizzi, sistemi operativi e token dei backend Ollama.

## Token del wrapper

Tutte le route del wrapper richiedono un Bearer token distinto dai token usati verso i singoli backend.

Impostarlo prima dell'avvio:

```bash
export OLLAMA_ROUTER_BEARER_TOKEN='INSERISCI_UN_TOKEN_LUNGO_E_CASUALE'
```

Per generarne uno:

```bash
openssl rand -hex 32
```

Avviare quindi il servizio:

```bash
uvicorn app:app --host 0.0.0.0 --port 11434
```

Se la variabile non viene impostata, viene usato il valore segnaposto presente in `app.py`; deve essere cambiato prima di esporre il servizio.

## Configurazione di Open WebUI

Configurare una sola connessione Ollama, puntata al wrapper, e impostare lo stesso token come Bearer/API key:

```text
http://IP-DEL-WRAPPER:11434
```

Il wrapper verifica il token ricevuto da Open WebUI e non lo inoltra ai backend. Per ogni backend usa invece il rispettivo `bearer_token` configurato in `SERVERS`.

## Routing

- Sono accettati solo modelli restituiti da `/api/ps` di almeno un backend.
- Se il modello è caricato su uno o più Linux, viene scelto casualmente un Linux.
- Altrimenti viene scelto casualmente uno dei macOS che lo hanno caricato.
- `/api/tags` e `/api/ps` espongono solo modelli caricati, deduplicati per nome.
- `/api/version` viene inoltrato al primo server configurato.
- Tutte le route, compresa `/health`, richiedono autenticazione Bearer.

## Verifica

```bash
TOKEN='INSERISCI_UN_TOKEN_LUNGO_E_CASUALE'

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
