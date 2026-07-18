# 🔌 MCP Server - Cognitive Computing

Questo repository contiene il server basato su **Model Context Protocol (MCP)** per il progetto principale di Cognitive Computing. 
Il server ha il compito di esporre come strumenti (tool) tutte le funzioni di interazione con i database persistenti (**Neo4j** e **ChromaDB**), isolando così la logica di accesso ai dati dal grafo multi-agente principale.

## 📁 Struttura della Repository

- `server_mcp.py`: Il file principale (entry point) che definisce e avvia il server MCP e i relativi tool.
- `pyproject.toml` / `uv.lock`: File di configurazione di `uv` per la gestione riproducibile delle dipendenze.
- `.env`: File contenente le variabili d'ambiente e i segreti per la connessione ai database.
- `.python-version`: Specifica la versione di Python supportata dal progetto.

## ⚡ Prerequisiti e Installazione

Questo progetto utilizza **`uv`** per una gestione rapida ed efficiente dell'ambiente virtuale e dei pacchetti.

1. Assicurati di avere `uv` installato sul tuo sistema.
2. Clona la repository e posizionati al suo interno.
3. Sincronizza le dipendenze:
   ```bash
   uv sync
   ```

## 🚀 Come avviare il Server

Per avviare il server MCP e metterlo in ascolto per l'agente principale, è sufficiente eseguire lo script dal terminale tramite `uv`:

```bash
uv run server_mcp.py
```

Una volta avviato, il server esporrà gli endpoint necessari al sistema LangGraph per eseguire operazioni di lettura, ricerca vettoriale e inserimento dati sui database in modo standardizzato e tracciabile.

## .env
NEO4J_URI=
NEO4J_USER=
NEO4J_PASSWORD=
NEO4J_DATABASE=
AURA_INSTANCEID=
AURA_INSTANCENAME=Free instance

CHROMA_API_KEY=
CHROMA_TENANT=
CHROMA_DATABASE=

GEMINI_API_KEY=