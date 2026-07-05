from mcp.server.fastmcp import FastMCP
from neo4j import GraphDatabase
import chromadb
from chromadb.utils import embedding_functions
import os
from dotenv import load_dotenv

load_dotenv()

mcp = FastMCP("MCP-Server-UniAgent")

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

try:
    driver_neo4j = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver_neo4j.verify_connectivity()
except Exception as e:
    raise RuntimeError(f"Impossibile connettersi a Neo4j all'avvio: {str(e)}")

try: 
    chroma_client = chromadb.CloudClient(
        api_key=os.getenv("CHROMA_API_KEY"),
        tenant=os.getenv("CHROMA_TENANT"),
        database=os.getenv("CHROMA_DATABASE")
    )
    google_ef = embedding_functions.GoogleGenaiEmbeddingFunction(
        model_name="gemini-embedding-2"
    )
    collection = chroma_client.get_or_create_collection(name="UniAgent_RAG", embedding_function=google_ef) # gemini-embedding into chroma
except Exception as e:
    print(f"Impossibile connettersi a chroma cloud. {e}")


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
    
    # DESCRIZIONE QUERY: Crea il nodo "ArticoloBlog"; Collega alla materia di riferimento il nodo;
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
    
    # DESCRIZIONE QUERY: Collega i "Concetti Teorici" all'articolo, utile in fase di retrieval. 
    concept_query = """
    MATCH (a:Articolo_Blog {nome: $title})
    MERGE (c:Concetto_Teorico {nome: $concept}) 
    MERGE (a)-[:SPIEGA]->(c) 
    """

    
    claim_query = """
    MERGE (claim:Affermazione {nome: $claim})
    
    WITH claim
    MATCH (a:Articolo_Blog {nome: $title})
    MERGE (a)-[:SOSTIENE]->(claim)
    
    WITH claim
    MERGE (c:Concetto_Teorico {nome: $concept})
    MERGE (claim)-[:RIGUARDA]->(c)
    """

    try:
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
        
        for concept in concepts:
            driver_neo4j.execute_query(
                concept_query, 
                parameters_={"title": title, "concept": concept}
            )
            
        # 3. SALVA LE RELAZIONI TRA I CONCETTI
        relationship_counter = 0
        for rel in concepts_relationships:
            
            # Nome della chiave imposta al modello.
            relationship_type = rel.get("relationship_type") 

            print(f"DEBUG NEEO {relationship_type}")

            # DESCRIZIONE QUERY: Collega i nodi "Concetti Teorici" tra loro. Le relazioni sono quelle trovate dal modello.
            relationship_query = f"""
            MERGE (c1:Concetto_Teorico {{nome: $origin}})
            MERGE (c2:Concetto_Teorico {{nome: $destination}})
            MERGE (c1)-[r:{relationship_type}]->(c2)
            SET r.detail = $detail
            """
            
            if relationship_type in RELAZIONI_AMMESSE:
                
                driver_neo4j.execute_query(
                    relationship_query, 
                    parameters_={
                        "origin": rel["origin"],           
                        "destination": rel["destination"],
                        "detail": rel["detail"]           
                    }
                )
                relationship_counter += 1


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
def search_topic_gap(specific_subject: str = "") -> str:
    """
    Analyzes the Knowledge Graph to return an overview of the written articles.
    Extracts titles, subjects, explained concepts, and the level of detail (via claims).
    To be used to understand the current state of content and suggest new topics (content gaps).
    """
        
    if specific_subject:
        subject_block = "MATCH (a)-[:APPARTIENE_A]->(m:Materia) WHERE m.nome = $subject"
    else:
        subject_block = "OPTIONAL MATCH (a)-[:APPARTIENE_A]->(m:Materia)"

    # DESCRIZIONE QUERY: Torna tutti gli articoli. CRITICITA': quando gli articoli crescono di numero, esplode tutto.
    query = f"""
    MATCH (a:Articolo_Blog)
    {subject_block}
    OPTIONAL MATCH (a)-[:SPIEGA]->(c:Concetto_Teorico)
    OPTIONAL MATCH (a)-[:SOSTIENE]->(claim:Affermazione)
    RETURN 
        a.nome AS title,
        a.data_creazione AS creation_date,
        m.nome AS subject,
        collect(DISTINCT c.nome) AS concepts,
        count(DISTINCT claim) AS number_of_claims,
        collect(DISTINCT claim.nome)[0..3] AS example_claims
    ORDER BY creation_date DESC
    """

    try:
        parameters = {}
        if specific_subject:
            parameters["subject"] = specific_subject

        records, _, _ = driver_neo4j.execute_query(query, parameters_=parameters)

        if not records:
            return "No articles found in the Knowledge Graph. It's time to write the first one!"

        total_articles = len(records)
        all_explored_concepts = set()

        result = "==================================================================\n"
        result += f"📊 ARTICLE COVERAGE REPORT (Total found: {total_articles})\n"
        result += "==================================================================\n\n"

        for rec in records:
            title = rec["title"]
            subject = rec["subject"] or "Not specified"
            concepts = rec["concepts"]
            num_claims = rec["number_of_claims"]
            claims = rec["example_claims"]

            # Raccogliamo i concetti in un set globale per avere l'ecosistema generale
            all_explored_concepts.update(concepts)

            result += f"📝 TITLE: {title}\n"
            result += f"   - Subject: {subject}\n"
            result += f"   - Key concepts explored ({len(concepts)}): {', '.join(concepts) if concepts else 'None'}\n"
            result += f"   - Level of detail: {num_claims} specific claims connected.\n"
            if claims:
                result += f"   - Examples of supported claims: {', '.join(claims)}...\n"
            result += "-" * 50 + "\n"

        result += f"\n🧠 GLOBAL MAP OF ALREADY EXPLORED CONCEPTS ({len(all_explored_concepts)} unique concepts):\n"
        result += ", ".join(all_explored_concepts) + "\n\n"
        result += "IMPLICIT INSTRUCTIONS FOR THE PLANNER: Analyze the list above. Find the 'blind spots' (missing topics or relationships between concepts) and propose the next titles to write."

        return result

    except Exception as e:
        return f"DB ERROR DURING COVERAGE ANALYSIS: {str(e)}"
    
@mcp.tool()
def neo4j_search(embedded_title: list[float], top_k: int) -> list[str]:
    """Ricerca semantica nel Knowledge Graph tramite vettore."""

    # DESCRIZIONE QUERY: Ricerca vettoriale tramite titolo sull'indice degli articoli; Filtro; Estrazione dei nodi "Concetti Teorici" ; Estrazione dei nodi "Affermazione" ; 
    query = """
    // 1. Ricerca vettoriale (il warning di deprecazione ignoralo per ora)
    CALL db.index.vector.queryNodes('article_vector_index', $top_k, $embedded_title)
    YIELD node AS a, score
    
    // 2. Filtro abbassato per debug (prima era 0.80, troppo aggressivo)
    WHERE score >= $threshold
    
    // 3. Modifica queste relazioni con quelle REALI che hai salvato nel DB! 
    // Esempio: se l'articolo è collegato ai concetti con un arco diverso, aggiornalo.
    OPTIONAL MATCH (a)-[]-(c:Concept)
    
    // 6. Aggregazione dei risultati (ATTENZIONE: usa a.title, non a.nome!)
    RETURN 
        a.title AS article,
        score AS similarita,
        collect(DISTINCT c.name) AS theorical_concepts
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
    """Esegue query sequenziali su ChromaDB"""
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