from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.runnables import RunnableMap
from langchain_google_genai import ChatGoogleGenerativeAI
import langchain_google_genai as genai
from langchain_huggingface import HuggingFaceEmbeddings
from langchain.prompts import PromptTemplate
from langchain_community.vectorstores import FAISS
from dotenv import load_dotenv
from PyPDF2 import PdfReader
from typing import List
import os, logging, pandas as pd
import time
import shutil
from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional
import streamlit as st



logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# load_dotenv('./.env', override=True)
# #os.environ["GOOGLE_API_KEY"] = os.getenv("GOOGLE_API_KEY")  #✅ Ensure env variable is set in code

google_api_key = st.secrets["GOOGLE_API_KEY"]
genai.configure(api_key=google_api_key)

logger.info(f"GOOGLE_API_KEY: {os.getenv('GOOGLE_API_KEY')}")
llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    api_key=os.getenv('GOOGLE_API_KEY')  # ✅ Use env variable
)
embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

# ✅ Define unified vector store path
UNIFIED_VECTOR_STORE = "./faiss_vectors/knowledge_base"

# Extract text from PDF
def extract_text_from_pdf(pdf_path: str) -> str:
    text = ""
    with open(pdf_path, "rb") as file:
        reader = PdfReader(file)
        for page in reader.pages:
            text += page.extract_text() or ""
    logger.info(f"Extracted {len(text)} characters from {pdf_path}")
    return text


# Split text into chunks
def split_text_into_chunks(text: str) -> List[str]:
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=80000, chunk_overlap=1000)
    return text_splitter.split_text(text)


# ✅ FIXED: Create or update vector store
def create_vector_store(vector_store_path: str, text_chunks: List[str]):
    """
    Create or update vector store with text chunks
    """
    new_vectors = FAISS.from_texts(text_chunks, embedding=embeddings)
    
    if os.path.exists(vector_store_path):
        logger.info(f"📂 Loading existing vector store from {vector_store_path}")
        existing_vectors = FAISS.load_local(vector_store_path, embeddings, allow_dangerous_deserialization=True)
        existing_vectors.merge_from(new_vectors)
        existing_vectors.save_local(vector_store_path)
        logger.info(f"✅ Vector store updated at {vector_store_path}")
    else:
        logger.info(f"📂 Creating new vector store at {vector_store_path}")
        os.makedirs(os.path.dirname(vector_store_path), exist_ok=True)
        new_vectors.save_local(vector_store_path)
        logger.info(f"✅ New vector store created at {vector_store_path}")


# ✅ FIXED: Upload PDFs once
def upload_pdfs_once(pdf_paths: List[str]):
    """
    Upload PDFs to knowledge base - call this ONCE
    """
    logger.info(f"\n{'='*60}")
    logger.info(f"📤 STARTING PDF UPLOAD PROCESS")
    logger.info(f"{'='*60}\n")
    
    success_count = 0
    uploaded_files = []
    failed_files = []
    
    for pdf_path in pdf_paths:
        try:
            logger.info(f"📄 Processing: {pdf_path}")
            
            if not os.path.exists(pdf_path):
                logger.error(f"❌ File not found: {pdf_path}")
                failed_files.append({"file": pdf_path, "error": "File not found"})
                continue
            
            text = extract_text_from_pdf(pdf_path)
            
            if not text.strip():
                logger.error(f"❌ PDF is empty: {pdf_path}")
                failed_files.append({"file": pdf_path, "error": "PDF is empty"})
                continue
            
            text_chunks = split_text_into_chunks(text)
            logger.info(f"📊 Created {len(text_chunks)} chunks from {pdf_path}")
            
            # ✅ FIXED: Pass vector_store_path as first parameter
            create_vector_store(UNIFIED_VECTOR_STORE, text_chunks)
            
            uploaded_files.append({
                "file": os.path.basename(pdf_path),
                "chunks": len(text_chunks),
                "characters": len(text),
                "timestamp": pd.Timestamp.now()
            })
            
            logger.info(f"✅ Successfully processed: {pdf_path}")
            success_count += 1
            
        except Exception as e:
            logger.error(f"❌ Error processing {pdf_path}: {str(e)}")
            failed_files.append({"file": pdf_path, "error": str(e)})
    
    # Save upload log
    if uploaded_files:
        df = pd.DataFrame(uploaded_files)
        log_file = "./pdf_upload_log.csv"
        
        if os.path.exists(log_file):
            existing_df = pd.read_csv(log_file)
            df = pd.concat([existing_df, df], ignore_index=True)
        
        df.to_csv(log_file, index=False)
        logger.info(f"📝 Upload log saved to {log_file}")
    
    logger.info(f"\n{'='*60}")
    logger.info(f"✅ UPLOAD COMPLETE")
    logger.info(f"   Successfully uploaded: {success_count}/{len(pdf_paths)} PDFs")
    logger.info(f"   Failed: {len(failed_files)} PDFs")
    logger.info(f"{'='*60}\n")
    
    if failed_files:
        logger.warning("❌ Failed files:")
        for item in failed_files:
            logger.warning(f"   - {item['file']}: {item['error']}")
    
    return {
        "success": success_count,
        "failed": len(failed_files),
        "uploaded_files": uploaded_files,
        "failed_files": failed_files
    }


# Process video transcription
def process_transcribed_video_text(vector_store_path, transcribed_text: str):
    try:
        logger.info(f"Processing Transcribed Text from Video")
        text_chunks = split_text_into_chunks(transcribed_text)
        create_vector_store(vector_store_path, text_chunks)
        return None
    except Exception as e:
        return f"Error processing transcribed text: {str(e)}"


# ✅ FIXED: Main query function
def get_insights_from_video(user_query, transcribed_text=None):
    """
    Query the knowledge base (PDFs + optional video transcriptions)
    """
    os.makedirs("./faiss_vectors", exist_ok=True)
    
    # ✅ Use unified vector store
    vector_store_path = UNIFIED_VECTOR_STORE
    
    # If video transcription provided, add it
    if transcribed_text:
        logger.info("Adding video transcription to knowledge base")
        result = process_transcribed_video_text(vector_store_path, transcribed_text)
        if result is not None:
            return result
        
        # Wait for it to be saved
        max_attempts = 12
        attempts = 0
        while attempts < max_attempts:
            if os.path.exists(vector_store_path):
                break
            time.sleep(5)
            attempts += 1
    
    # Check if knowledge base exists
    if not os.path.exists(vector_store_path):
        return "❌ No documents found. Please upload PDFs first using upload_pdfs_once() function."
    
    # Execute query
    try:
        vector_store = FAISS.load_local(vector_store_path, embeddings, allow_dangerous_deserialization=True)
        docs = vector_store.similarity_search(user_query, k=4)
        
        if not docs:
            return "<h3>The answer is not available in the context.</h3>"
        
        logger.info(f"Found {len(docs)} relevant documents")
        
        # Construct prompt
        prompt_template = """
        Answer the question as detailed as possible from the provided context.
        The context includes information from video transcriptions and PDF documents.
        
        If the answer is not in the provided context, or the user question is irrelevant, just say:
        "The answer for this question is not available in the context. Please try again with another relevant file or detach the attachment and ask your question again."
        
        If the {question} is a greeting, ignore the context and just say "Hey! I am here to help you with your video analysis and insights. Let's get started!".

        Context: {context}
        Question: {question}
        
        ### Output Format (markdown)
        
        - **Give response strictly in clean markdown format, always using proper headings and spacing.**
        - **Output must look user friendly and pretty.**

        **Strict Style Guidelines**
        - Always give tables with proper #ffffff (white) borders. Column names of table must be in black color with #ffffff (white) background
        - **Always write section headings in #ffffff (Black) color without any background color**
        - All the remaining text **must** always be in white color.
        
        Highlight keywords in bold using markdown bold tags **text**.
        """
        
        prompt = PromptTemplate(template=prompt_template, input_variables=["context", "question"])
        
        insights = (
            RunnableMap({
                "context": lambda _: docs,
                "question": lambda _: user_query,
            })
            | prompt 
            | llm
        )
        
        response = insights.invoke({"context": docs, "question": user_query})
        logger.info(f"Response Metadata: {response.usage_metadata}")
        
        result = response.content
        result = result.replace("```html", "").replace("```", "")
        
        # Save to CSV
        if os.path.exists("./transcribed_video_queries_responses.csv"):
            df = pd.read_csv("./transcribed_video_queries_responses.csv")
            new_entry = pd.DataFrame({'Query': [user_query], 'Response': [result]})
            df = pd.concat([df, new_entry], ignore_index=True)
        else:
            df = pd.DataFrame({'Query': [user_query], 'Response': [result]})
        df.to_csv("./transcribed_video_queries_responses.csv", index=False)
        
        return result
        
    except Exception as e:
        logger.error(f"Error during query execution: {str(e)}")
        return f"Error during query execution: {str(e)}"
app = FastAPI()

# Allow access from Streamlit frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    query: str
    transcription: Optional[str] = None

class UploadTranscription(BaseModel):
    text: str


@app.get("/")
def home():
    return {"message": "FastAPI chatbot backend is running!"}


# ----------------------------------------
# 📌 Upload PDFs to Knowledge Base
# ----------------------------------------
@app.post("/upload-pdfs")
async def upload_pdfs(files: List[UploadFile] = File(...)):
    saved_paths = []

    folder = "document"
    os.makedirs(folder, exist_ok=True)

    for file in files:
        path = os.path.join(folder, file.filename)

        with open(path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        saved_paths.append(path)

    result = upload_pdfs_once(saved_paths)
    return result


# ----------------------------------------
# 📌 Upload Video Transcription
# ----------------------------------------
@app.post("/upload-transcription")
async def upload_transcription(payload: UploadTranscription):
    response = process_transcribed_video_text(
        "./faiss_vectors/knowledge_base",
        payload.text
    )
    return {"message": "Transcription added", "error": response}


# ----------------------------------------
# 📌 Query Chatbot
# ----------------------------------------
@app.post("/chat")
async def chat_api(request: ChatRequest):
    answer = get_insights_from_video(
        request.query,
        request.transcription
    )
    return {"answer": answer}


# ----------------------------------------
# 📌 Knowledge Base Status
# ----------------------------------------
@app.get("/kb-status")
async def kb_status():
    return check_knowledge_base_status()

# ✅ Check knowledge base status
def check_knowledge_base_status():
    """
    Check if knowledge base exists and get info
    """
    if os.path.exists(UNIFIED_VECTOR_STORE):
        try:
            vector_store = FAISS.load_local(UNIFIED_VECTOR_STORE, embeddings, allow_dangerous_deserialization=True)
            doc_count = vector_store.index.ntotal
            
            logger.info(f"\n{'='*60}")
            logger.info(f"✅ KNOWLEDGE BASE STATUS")
            logger.info(f"   Status: EXISTS")
            logger.info(f"   Total chunks: {doc_count}")
            logger.info(f"   Location: {UNIFIED_VECTOR_STORE}")
            logger.info(f"{'='*60}\n")
            
            return {"exists": True, "total_chunks": doc_count}
        except Exception as e:
            logger.error(f"❌ Error loading knowledge base: {str(e)}")
            return {"exists": False, "error": str(e)}
    else:
        logger.warning(f"\n{'='*60}")
        logger.warning(f"❌ KNOWLEDGE BASE STATUS")
        logger.warning(f"   Status: NOT FOUND")
        logger.warning(f"   Location: {UNIFIED_VECTOR_STORE}")
        logger.warning(f"{'='*60}\n")
        return {"exists": False}


if __name__ == "__main__":
    
    # Check existing knowledge base
    print("\n🔍 Checking existing knowledge base...")
    status = check_knowledge_base_status()
    
    # Upload PDFs
    pdf_files = [
        "./document/Ebizframe Administration User manual.pdf",
        "./document/Ebizframe ai HR.pdf",
        "./document/Ebizframe Finance User manual.pdf",
        "./document/Ebizframe Inventory manual_new.pdf",
        "./document/Ebizframe Sales Manual_new.pdf",
        "./document/Ebizframe_Purchase_Manual New Document 2.0.pdf",
    ]
    
    print("\n📤 Starting PDF upload...")
    upload_result = upload_pdfs_once(pdf_files)
    
    # Check again after upload
    print("\n🔍 Verifying upload...")
    status = check_knowledge_base_status()
    
    # Test query
    print("\n🧪 Testing knowledge base...")
    answer = get_insights_from_video("What is Ebizframe?")
    print(f"\n📝 Answer: {answer[:200]}...")


