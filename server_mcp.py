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
    materia: str, vettore: list[float], fonti: list[str], relazioni_concetti: list[dict], claims_articolo: list[dict]
) -> str:
    """Salva nel grafo un articolo, il suo embedding, i link web come proprietà e lo collega a concetti e documenti originali."""
    
    # Query 1: Crea l'articolo con l'array di link e lo collega alla Materia
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

    # Query 3: Collega ai Documenti originali (PDF) citati
    query_fonti = """
    MATCH (a:Articolo_Blog {nome: $titolo})
    MATCH (d:Documento {nome: $documento}) 
    MERGE (a)-[:SI_BASA_SU]->(d)
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
            

        # 4. SALVA LE RELAZIONI TRA I CONCETTI (NUOVO BLOCCO)
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

        # 5. SALVA LE AFFERMAZIONI (CLAIMS) (NUOVO BLOCCO)
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
            
        return f"SUCCESSO: Salvato! {len(concetti_spiegati)} concetti, {contatore_relazioni} relazioni, {contatore_claims} claims."
    except Exception as e:
        return f"ERRORE SALVATAGGIO ARTICOLO: {str(e)}"
    

if __name__ == "__main__":
    mcp.run()