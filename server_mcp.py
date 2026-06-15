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
MACRO_AMMESSE = ["Materia", "Concetto_Teorico", "Componente_Tecnologico", "Processo_Algoritmo", "Persona", "Articolo_Blog", "Chunk_Testo", "Documento", "Affermazione"]
RELAZIONI_AMMESSE = ["APPARTIENE_A", "SI_BASA_SU", "È_UN_TIPO_DI", "COMPOSTO_DA", "RISOLVE_USA", "SPIEGA", "MENZIONATO_IN", "SOSTIENE", "RIGUARDA"]

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
def inserisci_articolo_agente(
    titolo: str, contenuto: str, concetti_spiegati: list[str], 
    materia: str, vettore: list[float], 
    fonti_documentali: list[str], link_esterni: list[str], relazioni_concetti: list[dict], claims_articolo: list[dict]
) -> str:
    """Salva nel grafo un articolo, il suo embedding, i link web come proprietà e lo collega a concetti e documenti originali."""
    
    # Query 1: Crea l'articolo con l'array di link e lo collega alla Materia
    query_articolo = """
    MERGE (a:Articolo_Blog {nome: $titolo})
    SET a.contenuto = $contenuto, 
        a.data_creazione = date(), 
        a.autore = 'Agente_AI',
        a.vettore = $vettore,
        a.link_esterni = $link_esterni
    WITH a
    MATCH (m:Materia {nome: $materia})
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
                "vettore": vettore, "link_esterni": link_esterni
            }
        )
        
        # Collega ai concetti
        for concetto in concetti_spiegati:
            driver_neo4j.execute_query(query_concetti, parameters_={"titolo": titolo, "concetto": concetto})
            
        # Collega ai documenti usati come fonte
        for doc in fonti_documentali:
            # Usiamo MATCH invece di MERGE per il Documento perché il PDF dovrebbe già esistere dall'ingestione
            driver_neo4j.execute_query(query_fonti, parameters_={"titolo": titolo, "documento": doc})

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
            
        return f"SUCCESSO: Salvato! {len(concetti_spiegati)} concetti, {contatore_relazioni} relazioni, {len(fonti_documentali)} fonti, {contatore_claims} claims."
    except Exception as e:
        return f"ERRORE SALVATAGGIO ARTICOLO: {str(e)}"
    
@mcp.tool()
def crea_claim(origine_id: str, testo_claim: str, concetto_riferimento: str) -> str:
    """
    Crea un'Affermazione chiave e la collega al Documento/Chunk/Articolo che la sostiene,
    e al Concetto Teorico a cui si riferisce.
    """
    query = """
    MERGE (claim:Affermazione {nome: $testo_claim})
    WITH claim
    MATCH (origine {nome: $origine_id}) // Può essere un Chunk o un Articolo_Blog
    MERGE (origine)-[r1:SOSTIENE]->(claim)
    WITH claim
    MATCH (concetto:Concetto_Teorico {nome: $concetto})
    MERGE (claim)-[r2:RIGUARDA]->(concetto)
    RETURN claim.nome
    """
    try:
        driver_neo4j.execute_query(
            query, 
            parameters_={
                "testo_claim": testo_claim,
                "origine_id": origine_id,
                "concetto": concetto_riferimento
            }
        )
        return f"SUCCESSO: Claim registrato e collegato a {origine_id}."
    except Exception as e:
        return f"ERRORE DB CLAIM: {str(e)}"

if __name__ == "__main__":
    mcp.run()