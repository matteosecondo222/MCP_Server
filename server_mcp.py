from mcp.server.fastmcp import FastMCP
from neo4j import GraphDatabase
import chromadb
from chromadb.utils import embedding_functions
import os
from dotenv import load_dotenv

# Carica le variabili d'ambiente dal file .env nella cartella del server
load_dotenv()

# Inizializza il server MCP
mcp = FastMCP("Provider-Grafo-Ingegneria")

# Connessione a Neo4j
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

# Apriamo la connessione UNA sola volta all'avvio del server.
try:
    driver_neo4j = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver_neo4j.verify_connectivity()
except Exception as e:
    raise RuntimeError(f"Impossibile connettersi a Neo4j all'avvio: {str(e)}")

# Connessione ChromaDB
try: 
    chroma_client = chromadb.CloudClient(
        api_key=os.getenv("CHROMA_API_KEY"),
        tenant=os.getenv("CHROMA_TENANT"),
        database=os.getenv("CHROMA_DATABASE")
    )
    google_ef = embedding_functions.GoogleGenaiEmbeddingFunction(
        model_name="gemini-embedding-2"
    )
    collection = chroma_client.get_or_create_collection(name="UniAgent_RAG", embedding_function=google_ef)
except Exception as e:
    print(f"Impossibile connettersi a chroma cloud. {e}")


# --- REGOLE DELL'ONTOLOGIA ---
MACRO_AMMESSE = ["Materia", "Concetto_Teorico", "Componente_Tecnologico", "Processo_Algoritmo", "Articolo_Blog", "Affermazione"]
RELAZIONI_AMMESSE = ["APPARTIENE_A", "SI_BASA_SU", "È_UN_TIPO_DI", "COMPOSTO_DA", "RISOLVE", "USA", "SPIEGA", "MENZIONATO_IN", "SOSTIENE", "RIGUARDA"]
    
@mcp.tool()
def insert_article(
    title: str, 
    content: str, 
    concepts: list[str], 
    subject: str, 
    title_embedding: list[float], 
    sources: list[str], 
    concepts_relationships: list[dict], 
    claims: list[dict]
) -> str:
    """Salva nel grafo un articolo, il suo embedding, i link web come proprietà e lo collega a concetti e documenti originali."""
    
    # Query 1: Crea l'articolo con l'array di fonti e lo collega alla Materia
    article_query = """
    MERGE (a:Articolo_Blog {nome: $title})
    SET a.content = $content, 
        a.createdAt = date(), 
        a.author = 'AI_Agent',
        a.title_embedding = $title_embedding,
        a.sources = $sources
    WITH a
    MERGE (m:Materia {nome: $subject})
    MERGE (a)-[:APPARTIENE_A]->(m)
    RETURN a.nome
    """
    
    # Query 2: Collega ai concetti
    concept_query = """
    MATCH (a:Articolo_Blog {nome: $title})
    MERGE (c:Concetto_Teorico {nome: $concept}) 
    MERGE (a)-[:SPIEGA]->(c) 
    """
    
    try:
        # Esegue la creazione principale dell'articolo
        driver_neo4j.execute_query(
            article_query, 
            parameters_={
                "title": title, 
                "content": content, 
                "subject": subject, 
                "title_embedding": title_embedding, 
                "sources": sources
            }
        )
        
        # Collega ai concetti
        for concept in concepts:
            driver_neo4j.execute_query(
                concept_query, 
                parameters_={"title": title, "concept": concept}
            )
            
        # 3. SALVA LE RELAZIONI TRA I CONCETTI
        relationship_counter = 0
        for rel in concepts_relationships:
            # Assumiamo che la chiave sia diventata "relationship_type" nel JSON dell'agente
            relationship_type = rel.get("relationship_type") 
            
            # Controllo di sicurezza fondamentale
            if relationship_type in RELAZIONI_AMMESSE:
                relationship_query = f"""
                MERGE (c1:Concetto_Teorico {{nome: $origin}})
                MERGE (c2:Concetto_Teorico {{nome: $destination}})
                MERGE (c1)-[r:{relationship_type}]->(c2)
                SET r.detail = $detail
                """
                driver_neo4j.execute_query(
                    relationship_query, 
                    parameters_={
                        "origin": rel["origin"],           # Aggiornato in inglese
                        "destination": rel["destination"], # Aggiornato in inglese
                        "detail": rel["detail"]            # Aggiornato in inglese
                    }
                )
                relationship_counter += 1

        # 4. SALVA LE AFFERMAZIONI (CLAIMS)
        claim_query = """
            MERGE (claim:Affermazione {nome: $claim})
            WITH claim
            MATCH (a:Articolo_Blog {nome: $title})
            MERGE (a)-[:SOSTIENE]->(claim)
            WITH claim
            MATCH (c:Concetto_Teorico {nome: $concept})
            MERGE (claim)-[:RIGUARDA]->(c)
            """

        claim_counter = 0
        for claim_item in claims:
            driver_neo4j.execute_query(
                claim_query,
                parameters_={
                    "claim": claim_item["claim"],
                    "title": title,
                    "concept": claim_item["concept_reference"]
                }
            )
            claim_counter += 1
            
        return f"SUCCESSO: Salvato! {len(concepts)} concetti, {relationship_counter} relazioni, {len(sources)} fonti, {claim_counter} claims."
    
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
    
@mcp.tool()
def neo4j_search(embedded_title: list[float], top_k: int) -> list[str]:
    """Ricerca semantica nel Knowledge Graph tramite vettore."""
    query = """
    // 1. Punto di ingresso: Ricerca vettoriale sull'indice degli articoli
    CALL db.index.vector.queryNodes('article_vector_index', $top_k, $embedded_title)
    YIELD node AS a, score
    
    // 2. Filtro di sicurezza (Barriera anti-allucinazione)
    WHERE score >= $threshold
    
    // 3. Estrazione dei Concetti Spiegati
    OPTIONAL MATCH (a)-[:SPIEGA]->(c:Concetto_Teorico)
    
    // 4. Estrazione delle Affermazioni (Claims) relative a quei concetti
    OPTIONAL MATCH (a)-[:SOSTIENE]->(claim:Affermazione)-[:RIGUARDA]->(c)
    
    // 5. Estrazione dei Concetti Correlati (vicini nel grafo)
    OPTIONAL MATCH (c)-[]-(c_correlato:Concetto_Teorico)
    WHERE c_correlato <> c AND c_correlato IS NOT NULL
    
    // 6. Aggregazione dei risultati
    RETURN 
        a.nome AS article,
        score AS similarita,
        collect(DISTINCT c.nome) AS theorical_concepts,
        collect(DISTINCT claim.nome) AS key_claims,
        collect(DISTINCT c_correlato.nome) AS related_concepts
    ORDER BY score DESC
    """
    result = []
    try: 
        records, _, _ = driver_neo4j.execute_query(
            query, 
            parameters_={
                "embedded_title" : embedded_title,
                "top_k" : top_k,
                "threshold" : 0.80
            }
        )

        if records:
            for r in records:
                record = r.data()
                neo4j_result = f"Trovato articolo: {record['article']}\n"
                neo4j_result += f"Concetti correlati: {record['theorical_concepts']}\n"
                neo4j_result += f"Affermazioni Chiave: {record['key_claims']}\n"
                neo4j_result += f"Concetti Correlati: {record['related_concepts']}\n"
                neo4j_result += "--------------------------------"
                result.append(neo4j_result)
    except Exception as e: 
        print(f"Errore nella query Neo4j. {e}")
    
    print(f"\n\n INFORMAZIONI DAL KNOWLEDGE GRAPH : {result}\n\n")
    return result


@mcp.tool()
def rag_search(queries: list[str], subject: str, top_k: int = 3) -> dict:
    """Esegue query sequenziali su ChromaDB, aggirando il bug di batching dell'Embedding di Google."""
    chunk_unici = {}
    
    subject_variations = [
        subject,
        subject.lower(),
        subject.upper(),
        subject.title(),
        subject.capitalize()
    ]

    if not queries:
        return {"query": "", "results": []}
    
    for q in queries:
        try:
            rag_results = collection.query(
                query_texts=[q], 
                n_results=top_k,
                where={"subject": {"$in": subject_variations}} 
            )

            if rag_results and rag_results.get('documents') and rag_results['documents'][0]:
                documenti = rag_results['documents'][0]
                ids = rag_results['ids'][0]
                
                if rag_results.get('metadatas') and rag_results['metadatas'][0]:
                    metadati = rag_results['metadatas'][0]
                else:
                    metadati = [{}] * len(documenti)
                
                for doc_id, doc_text, meta in zip(ids, documenti, metadati):
                    if doc_id not in chunk_unici:
                        chunk_unici[doc_id] = {
                            "source": meta.get("source", "Sconosciuta"),
                            "content": doc_text,
                            "id" : doc_id
                        }
        except Exception as e:
            print(f"🔥 Errore ChromaDB sulla query '{q}': {e}")
            
    return {
        "query": " | ".join(queries), 
        "results": list(chunk_unici.values())
    }

if __name__ == "__main__":
    #mcp.run()
    print("🚀 Avvio del Server MCP su HTTP/SSE (Porta 8000)...")
    mcp.run(transport="sse")