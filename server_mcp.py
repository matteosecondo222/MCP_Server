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
MACRO_AMMESSE = ["Materia", "Concetto_Teorico", "Componente_Tecnologico", "Processo_Algoritmo", "Persona", "Articolo_Blog", "Chunk_Testo", "Documento", "Affermazione"]
RELAZIONI_AMMESSE = ["APPARTIENE_A", "SI_BASA_SU", "È_UN_TIPO_DI", "COMPOSTO_DA", "RISOLVE_USA", "SPIEGA", "MENZIONATO_IN", "SOSTIENE", "RIGUARDA"]



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

@mcp.tool()
def ricerca_ibrida_krag(vettore_query: list[float], top_k: int = 5) -> str:
    """
    Esegue una Ricerca Ibrida Multi-Livello in Neo4j.
    Cerca i testi più simili tramite Vector Index, poi espande la ricerca
    ai concetti teorici correlati, ai documenti sorgente e alle affermazioni chiave.
    """
    
    # NOTA: Assicurati di aver creato in Neo4j i due indici:
    """CREATE VECTOR INDEX chunk_vector_index FOR (c:Chunk_Testo) ON (c.vettore)
    OPTIONS {indexConfig: {`vector.dimensions`: 3072, `vector.similarity_function`: 'cosine'}};

    CREATE VECTOR INDEX article_vector_index FOR (a:Articolo_Blog) ON (a.vettore)
    OPTIONS {indexConfig: {`vector.dimensions`: 3072, `vector.similarity_function`: 'cosine'}};"""
    
    query_ibrida = """
    CALL {
      // 1A. Cerca i chunk dei PDF più pertinenti
      CALL db.index.vector.queryNodes('chunk_vector_index', $top_k, $vettore_query)
      YIELD node AS seed_node, score
      RETURN seed_node, score, "Chunk" AS origine
      
      UNION
      
      // 1B. Cerca tra i vecchi articoli di blog
      CALL db.index.vector.queryNodes('article_vector_index', $top_k, $vettore_query)
      YIELD node AS seed_node, score
      RETURN seed_node, score, "Articolo" AS origine
    }

    WITH seed_node, score, origine
    ORDER BY score DESC
    LIMIT $top_k

    // 2. ESPANSIONE: Naviga verso i concetti teorici (fino a 2 salti)
    OPTIONAL MATCH (seed_node)-[:APPARTIENE_A|SPIEGA|MENZIONATO_IN*1..2]-(concetto:Concetto_Teorico)

    // 3. ESTRAZIONE CLAIMS: Trova le affermazioni collegate
    OPTIONAL MATCH (claim:Affermazione)-[:RIGUARDA]->(concetto)

    // 4. ESTRAZIONE FONTI: Risale ai PDF
    OPTIONAL MATCH (seed_node)-[:APPARTIENE_A|SI_BASA_SU]->(doc:Documento)

    RETURN 
        origine AS tipo,
        seed_node.nome AS identificativo,
        COALESCE(seed_node.testo, seed_node.contenuto) AS testo_corpo,
        score,
        collect(DISTINCT concetto.nome) AS concetti_chiave,
        collect(DISTINCT claim.nome) AS tesi_e_claims,
        collect(DISTINCT doc.nome) AS file_pdf
    """
    
    try:
        records, _, _ = driver_neo4j.execute_query(
            query_ibrida, 
            parameters_={"vettore_query": vettore_query, "top_k": top_k}
        )
        
        if not records:
            return "Nessun risultato rilevante trovato nel Knowledge Graph."
            
        # Formattazione per l'LLM (Planner/Writer)
        risultato = "==================================================================\n"
        risultato += "PACCHETTO DI CONTESTO K-RAG STRUTTURATO\n"
        risultato += "==================================================================\n\n"
        
        for idx, rec in enumerate(records):
            tipo = rec['tipo']
            fonti = ", ".join(rec['file_pdf']) if rec['file_pdf'] else "Nessuna fonte diretta"
            
            risultato += f"[{idx+1}] FONTE ({tipo}): {fonti} (Rilevanza: {rec['score']:.2f})\n"
            risultato += f"ID/Titolo: {rec['identificativo']}\n"
            
            if rec['concetti_chiave']:
                risultato += f"Concetti correlati: {', '.join(rec['concetti_chiave'])}\n"
            
            if rec['tesi_e_claims']:
                risultato += "Affermazioni chiave (Claims):\n"
                for tesi in rec['tesi_e_claims']:
                    risultato += f"  - {tesi}\n"
                    
            risultato += f"Testo estratto:\n{rec['testo_corpo']}\n"
            risultato += "-" * 50 + "\n\n"
            
        return risultato
        
    except Exception as e:
        return f"ERRORE NELLA RICERCA IBRIDA: {str(e)}"

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