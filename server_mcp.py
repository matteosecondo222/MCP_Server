from mcp.server.fastmcp import FastMCP
from neo4j import GraphDatabase
import os
from dotenv import load_dotenv

# Carica le variabili d'ambiente dal file .env nella cartella del server
load_dotenv()

# Inizializza il server MCP
mcp = FastMCP("Provider-Grafo-Ingegneria")

# Credenziali Neo4j prese dal file .env
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")


# Apriamo la connessione UNA sola volta all'avvio del server.
try:
    driver_neo4j = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver_neo4j.verify_connectivity()
except Exception as e:
    raise RuntimeError(f"Impossibile connettersi a Neo4j all'avvio: {str(e)}")


# --- REGOLE DELL'ONTOLOGIA ---
MACRO_AMMESSE = ["Materia", "Concetto_Teorico", "Componente_Tecnologico", "Processo_Algoritmo", "Articolo_Blog", "Affermazione"]
RELAZIONI_AMMESSE = ["APPARTIENE_A", "SI_BASA_SU", "È_UN_TIPO_DI", "COMPOSTO_DA", "RISOLVE", "USA", "SPIEGA", "MENZIONATO_IN", "SOSTIENE", "RIGUARDA"]
    
@mcp.tool()
def inserisci_articolo_agente(
    titolo: str, contenuto: str, concetti_spiegati: list[str], 
    materia: str, vettore: list[float], 
    fonti: list[str], relazioni_concetti: list[dict], claims_articolo: list[dict]
) -> str:
    """Salva nel grafo un articolo, il suo embedding, i link web come proprietà e lo collega a concetti e documenti originali."""
    
    # Query 1: Crea l'articolo con l'array di fonti e lo collega alla Materia
    query_articolo = """
    MERGE (a:Articolo_Blog {nome: $titolo})
    SET a.contenuto = $contenuto, 
        a.data_creazione = date(), 
        a.autore = 'Agente_AI',
        a.vettore = $vettore,
        a.fonti = $fonti
    WITH a
    MERGE (m:Materia {nome: $materia})
    MERGE (a)-[:APPARTIENE_A]->(m)
    RETURN a.nome
    """
    
    # Query 2: Collega ai concetti
    query_concetti = """
    MATCH (a:Articolo_Blog {nome: $titolo})
    MERGE (c:Concetto_Teorico {nome: $concetto}) 
    MERGE (a)-[:SPIEGA]->(c) 
    """
    
    try:
        # Esegue la creazione principale dell'articolo
        driver_neo4j.execute_query(
            query_articolo, 
            parameters_={
                "titolo": titolo, "contenuto": contenuto, "materia": materia, 
                "vettore": vettore, "fonti": fonti
            }
        )
        
        # Collega ai concetti
        for concetto in concetti_spiegati:
            driver_neo4j.execute_query(query_concetti, parameters_={"titolo": titolo, "concetto": concetto})
            

        # 3. SALVA LE RELAZIONI TRA I CONCETTI (NUOVO BLOCCO)
        contatore_relazioni = 0
        for rel in relazioni_concetti:
            tipo_rel = rel.get("tipo_relazione")
            
            # Controllo di sicurezza fondamentale
            if tipo_rel in RELAZIONI_AMMESSE:
                query_rel_interna = f"""
                MERGE (c1:Concetto_Teorico {{nome: $origine}})
                MERGE (c2:Concetto_Teorico {{nome: $destinazione}})
                MERGE (c1)-[r:{tipo_rel}]->(c2)
                SET r.dettaglio = $dettaglio
                """
                driver_neo4j.execute_query(
                    query_rel_interna, 
                    parameters_={
                        "origine": rel["origine"], 
                        "destinazione": rel["destinazione"], 
                        "dettaglio": rel["dettaglio"]
                    }
                )
                contatore_relazioni += 1

        # 4. SALVA LE AFFERMAZIONI (CLAIMS) (NUOVO BLOCCO)
        query_claim = """
            MERGE (claim:Affermazione {nome: $testo_claim})
            WITH claim
            MATCH (a:Articolo_Blog {nome: $titolo})
            MERGE (a)-[:SOSTIENE]->(claim)
            WITH claim
            MATCH (c:Concetto_Teorico {nome: $concetto})
            MERGE (claim)-[:RIGUARDA]->(c)
            """

        contatore_claims = 0
        for claim in claims_articolo:
            driver_neo4j.execute_query(
                query_claim,
                parameters_={
                    "testo_claim": claim["affermazione"],
                    "titolo": titolo,
                    "concetto": claim["concetto_riferimento"]
                }
            )
            contatore_claims += 1
            
        return f"SUCCESSO: Salvato! {len(concetti_spiegati)} concetti, {contatore_relazioni} relazioni, {len(fonti)} fonti, {contatore_claims} claims."
    except Exception as e:
        return f"ERRORE SALVATAGGIO ARTICOLO: {str(e)}"
    

@mcp.tool()
def ricerca_topic_gap(materia_specifica: str = "") -> str:
    """
    Analizza il Knowledge Graph per restituire una panoramica degli articoli scritti.
    Estrae titoli, materie, concetti spiegati e il livello di dettaglio (tramite le affermazioni).
    Da usare per capire lo stato attuale dei contenuti e suggerire nuovi topic (content gap).
    """
    
    # FIX: Se c'è una materia, usiamo un MATCH rigido per tagliare fuori gli altri articoli.
    # Se non c'è, usiamo un OPTIONAL MATCH per prenderli tutti.
    if materia_specifica:
        blocco_materia = "MATCH (a)-[:APPARTIENE_A]->(m:Materia) WHERE m.nome = $materia"
    else:
        blocco_materia = "OPTIONAL MATCH (a)-[:APPARTIENE_A]->(m:Materia)"

    # Costruiamo la query iniettando il blocco corretto
    query = f"""
    MATCH (a:Articolo_Blog)
    {blocco_materia}
    OPTIONAL MATCH (a)-[:SPIEGA]->(c:Concetto_Teorico)
    OPTIONAL MATCH (a)-[:SOSTIENE]->(claim:Affermazione)
    RETURN 
        a.nome AS titolo,
        a.data_creazione AS data_creazione,
        m.nome AS materia,
        collect(DISTINCT c.nome) AS concetti,
        count(DISTINCT claim) AS numero_claims,
        collect(DISTINCT claim.nome)[0..3] AS esempi_claims
    ORDER BY data_creazione DESC
    """

    try:
        parameters = {}
        if materia_specifica:
            parameters["materia"] = materia_specifica

        records, _, _ = driver_neo4j.execute_query(query, parameters_=parameters)

        if not records:
            return "Nessun articolo trovato nel Knowledge Graph. È il momento di scrivere il primo!"

        totale_articoli = len(records)
        tutti_concetti_trattati = set()

        # Formattazione strutturata per l'LLM
        risultato = "==================================================================\n"
        risultato += f"📊 REPORT COPERTURA ARTICOLI (Totale trovati: {totale_articoli})\n"
        risultato += "==================================================================\n\n"

        for rec in records:
            titolo = rec["titolo"]
            materia = rec["materia"] or "Non specificata"
            concetti = rec["concetti"]
            num_claims = rec["numero_claims"]
            claims = rec["esempi_claims"]

            # Raccogliamo i concetti in un set globale per capire l'ecosistema generale
            tutti_concetti_trattati.update(concetti)

            risultato += f"📝 TITOLO: {titolo}\n"
            risultato += f"   - Materia: {materia}\n"
            risultato += f"   - Concetti chiave esplorati ({len(concetti)}): {', '.join(concetti) if concetti else 'Nessuno'}\n"
            risultato += f"   - Livello di dettaglio: {num_claims} affermazioni (claims) specifiche collegate.\n"
            if claims:
                risultato += f"   - Esempi di tesi sostenute: {', '.join(claims)}...\n"
            risultato += "-" * 50 + "\n"

        # Aggiungiamo un riepilogo finale che fa da "assist" all'LLM
        risultato += f"\n🧠 MAPPA GLOBALE DEI CONCETTI GIA' ESPLORATI ({len(tutti_concetti_trattati)} concetti unici):\n"
        risultato += ", ".join(tutti_concetti_trattati) + "\n\n"
        risultato += "ISTRUZIONI IMPLICITE PER IL PLANNER: Analizza l'elenco qui sopra. Trova i 'punti ciechi' (argomenti o relazioni mancanti tra i concetti) e proponi i prossimi titoli da scrivere."

        return risultato

    except Exception as e:
        return f"ERRORE DB DURANTE L'ANALISI DELLA COPERTURA: {str(e)}"

if __name__ == "__main__":
    mcp.run()