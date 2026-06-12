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

# ==========================================
# DRIVER GLOBALE (La soluzione ai blocchi)
# ==========================================
# Apriamo la connessione UNA sola volta all'avvio del server.
# Se la password è sbagliata o il DB è spento, si fermerà qui con un errore chiaro.
try:
    driver_neo4j = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver_neo4j.verify_connectivity()
except Exception as e:
    raise RuntimeError(f"Impossibile connettersi a Neo4j all'avvio: {str(e)}")


# --- REGOLE DELL'ONTOLOGIA ---
MACRO_AMMESSE = ["Materia", "Concetto_Teorico", "Componente_Tecnologico", "Processo_Algoritmo", "Persona", "Articolo_Blog", "Chunk_Testo", "Documento"]
RELAZIONI_AMMESSE = ["APPARTIENE_A", "SI_BASA_SU", "È_UN_TIPO_DI", "COMPOSTO_DA", "RISOLVE_USA", "SPIEGA", "MENZIONATO_IN"]

# ==========================================
# TOOLS (Strumenti reali per il Client/LLM)
# ==========================================

@mcp.tool()
def crea_nodo(macro_categoria: str, nome_entita: str, micro_categoria: str, descrizione_breve: str) -> str:
    """Inserisce o aggiorna una entità nel database a grafi."""
    if macro_categoria not in MACRO_AMMESSE:
        return f"ERRORE: La macro_categoria '{macro_categoria}' non è permessa."
    
    query = f"""
    MERGE (n:{macro_categoria} {{nome: $nome}})
    SET n.micro_categoria = $micro, n.descrizione_breve = $desc
    RETURN n.nome
    """
    try:
        # Usiamo il driver globale e parameters_ per il Type Checking
        driver_neo4j.execute_query(
            query, 
            parameters_={"nome": nome_entita, "micro": micro_categoria, "desc": descrizione_breve}
        )
        return f"SUCCESSO: Nodo '{nome_entita}' creato."
    except Exception as e:
        return f"ERRORE DB: {str(e)}"

@mcp.tool()
def crea_relazione(entita_origine: str, tipo_relazione: str, entita_destinazione: str, dettaglio: str) -> str:
    """Crea un collegamento direzionale tra due nodi (possono essere concetti, materie o chunk)."""
    if tipo_relazione not in RELAZIONI_AMMESSE:
        return f"ERRORE: La relazione '{tipo_relazione}' non è permessa."
    
    query = f"""
    MATCH (a {{nome: $origine}}), (b {{nome: $destinazione}})
    MERGE (a)-[r:{tipo_relazione}]->(b)
    SET r.dettaglio = $dettaglio
    RETURN type(r)
    """
    try:
        records, _, _ = driver_neo4j.execute_query(
            query, 
            parameters_={"origine": entita_origine, "destinazione": entita_destinazione, "dettaglio": dettaglio}
        )
        if not records:
            return f"ATTENZIONE: Nodo '{entita_origine}' o '{entita_destinazione}' mancante."
        return f"SUCCESSO: Relazione '{entita_origine}' -[{tipo_relazione}]-> '{entita_destinazione}' creata."
    except Exception as e:
        return f"ERRORE DB: {str(e)}"

@mcp.tool()
def crea_nodo_chunk(chunk_id: str, testo: str, vettore: list[float], sorgente: str) -> str:
    """Salva un blocco di testo fisico estratto da un PDF con il suo embedding vettoriale."""
    # Nota: in Neo4j i vettori sono semplici liste di float. 
    # Assicurati di aver creato in precedenza un Vector Index nel tuo Neo4j per query di similarità veloci.
    query = """
    MERGE (c:Chunk_Testo {nome: $chunk_id})
    SET c.testo = $testo, c.vettore = $vettore, c.sorgente = $sorgente
    RETURN c.nome
    """
    try:
        driver_neo4j.execute_query(
            query, 
            parameters_={"chunk_id": chunk_id, "testo": testo, "vettore": vettore, "sorgente": sorgente}
        )
        return f"SUCCESSO: Chunk vettoriale '{chunk_id}' salvato."
    except Exception as e:
        return f"ERRORE DB: {str(e)}"

@mcp.tool()
def verifica_documento(hash_file: str) -> str:
    """Verifica se un documento con questo hash è già stato processato."""
    query = "MATCH (d:Documento {hash: $hash}) RETURN d.nome LIMIT 1"
    try:
        records, _, _ = driver_neo4j.execute_query(query, parameters_={"hash": hash_file})
        if records:
            return "ESISTE"
        return "NUOVO"
    except Exception as e:
        return f"ERRORE DB: {str(e)}"

@mcp.tool()
def registra_documento(nome_file: str, hash_file: str, nome_materia: str) -> str:
    """Crea il nodo Documento con l'hash e lo collega alla sua Materia."""
    query = """
    MERGE (d:Documento {nome: $nome})
    SET d.hash = $hash, d.micro_categoria = 'PDF', d.data_elaborazione = date()
    WITH d
    MATCH (m:Materia {nome: $materia})
    MERGE (d)-[:APPARTIENE_A]->(m)
    RETURN d.nome
    """
    try:
        driver_neo4j.execute_query(query, parameters_={"hash": hash_file, "nome": nome_file, "materia": nome_materia})
        return f"SUCCESSO: Documento {nome_file} creato e collegato a {nome_materia}."
    except Exception as e:
        return f"ERRORE DB: {str(e)}"
    
@mcp.tool()
def inserisci_articolo_agente(titolo: str, contenuto: str, concetti_spiegati: list[str], materia: str) -> str:
    """Salva nel grafo un articolo generato dall'Agente, collegandolo ai concetti e alla Materia madre."""
    
    # Query 1: Crea l'articolo e lo collega SUBITO alla Materia
    query_articolo = """
    MERGE (a:Articolo_Blog {nome: $titolo})
    SET a.contenuto = $contenuto, a.data_creazione = date(), a.autore = 'Agente_AI'
    WITH a
    MATCH (m:Materia {nome: $materia})
    MERGE (a)-[:APPARTIENE_A]->(m)
    RETURN a.nome
    """
    
    # Query 2: Collega l'articolo ai singoli concetti teorici
    query_relazione = """
    MATCH (a:Articolo_Blog {nome: $titolo})
    MERGE (c:Concetto_Teorico {nome: $concetto}) 
    MERGE (a)-[r:SPIEGA]->(c) 
    RETURN type(r)
    """
    
    try:
        # Eseguiamo la prima query passando anche la materia
        driver_neo4j.execute_query(query_articolo, parameters_={"titolo": titolo, "contenuto": contenuto, "materia": materia})
        
        # Eseguiamo il ciclo per i concetti
        for concetto in concetti_spiegati:
            driver_neo4j.execute_query(query_relazione, parameters_={"titolo": titolo, "concetto": concetto})
            
        return f"SUCCESSO: Articolo salvato, agganciato a '{materia}' e collegato a {len(concetti_spiegati)} concetti."
    except Exception as e:
        return f"ERRORE SALVATAGGIO ARTICOLO: {str(e)}"

if __name__ == "__main__":
    mcp.run()