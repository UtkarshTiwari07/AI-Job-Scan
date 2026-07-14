import asyncio
import json
import os
import requests
import warnings
from dotenv import load_dotenv

# Load .env from parent directory automatically
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))
from typing import List, Optional
from pydantic import BaseModel, Field

# Silence that annoying requests/urllib3 warning cluttering your terminal!
warnings.filterwarnings("ignore", message="urllib3 .* doesn't match a supported version!")

# Crawl4AI Imports
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode, LLMConfig
from crawl4ai.extraction_strategy import LLMExtractionStrategy

# LangChain Imports
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
SERPER_API_KEY = os.getenv("SERPER_API_KEY")

# ==========================================
# PHASE 1: AUTONOMOUS SEARCH (SERPER.DEV)
# ==========================================

def search_for_jobs() -> List[str]:
    if not SERPER_API_KEY:
        print("❌ ERROR: SERPER_API_KEY environment variable is not set!")
        return []

    print("🔍 Phase 1: Agent is searching Google via Serper.dev for fresh jobs...")
    
    queries = [
        "site:weworkremotely.com/remote-jobs 'AI' OR 'LLM' OR 'Python'",
        "site:wellfound.com/role 'AI Engineer' freelance OR contract",
        "freelance AI voice agent developer jobs remote -upwork -fiverr", 
        "contract Python developer LiveKit Deepgram remote"
    ]
    
    found_urls = set()
    url = "https://google.serper.dev/search"
    headers = {
        'X-API-KEY': SERPER_API_KEY,
        'Content-Type': 'application/json'
    }
    
    for query in queries:
        print(f"   -> Querying Serper: {query}")
        payload = json.dumps({
            "q": query,
            "num": 4, 
            "tbs": "qdr:w" 
        })
        
        try:
            response = requests.post(url, headers=headers, data=payload)
            response.raise_for_status()
            results = response.json().get("organic", [])
            
            for r in results:
                link = r.get("link", "")
                ignore_list = ["upwork.com", "fiverr.com", "login", "signup", "freelancer.com"]
                if link and not any(bad in link.lower() for bad in ignore_list):
                    found_urls.add(link)
                    
        except Exception as e:
            print(f"⚠️ Serper search failed for '{query}': {e}")
            
    print(f"🎯 Found {len(found_urls)} unique job pages to analyze.")
    return list(found_urls)

# ==========================================
# PHASE 2: UNIVERSAL SCRAPE (READING UNKNOWN SITES)
# ==========================================

class JobPosting(BaseModel):
    job_title: str
    company_or_client: str
    description: str
    budget: str
    application_url: str

async def extract_jobs_from_urls(urls: List[str]) -> List[dict]:
    print(f"🕷️ Phase 2: Agent is visiting and reading the {len(urls)} websites...")
    
    browser_config = BrowserConfig(headless=True)
    
    extraction_strategy = LLMExtractionStrategy(
        llm_config=LLMConfig(
            provider="deepseek/deepseek-chat",
            api_token=DEEPSEEK_API_KEY
        ),
        schema=JobPosting.model_json_schema(),
        extraction_type="schema",
        instruction="Extract the freelance job posting details from this page. If this page is not a job posting, return empty.",
    )

    run_config = CrawlerRunConfig(
        extraction_strategy=extraction_strategy,
        cache_mode=CacheMode.BYPASS,
        magic=True 
    )

    all_jobs = []
    
    async with AsyncWebCrawler(config=browser_config) as crawler:
        results = await crawler.arun_many(urls=urls, config=run_config)
        
        for result in results:
            if result.success and result.extracted_content:
                try:
                    jobs = json.loads(result.extracted_content)
                    if isinstance(jobs, list):
                        all_jobs.extend(jobs)
                    elif isinstance(jobs, dict):
                        all_jobs.append(jobs)
                except json.JSONDecodeError:
                    pass

    print(f"✅ Successfully extracted {len(all_jobs)} job objects from the wild web.")
    return all_jobs

# ==========================================
# PHASE 3: EVALUATION & PROPOSAL (DEEPSEEK V3)
# ==========================================

class EvaluatedJob(BaseModel):
    is_match: bool
    job_title: str
    company: str = ""
    application_url: str = ""
    rejection_reason: Optional[str] = None   # DeepSeek may omit for matched jobs
    drafted_proposal: Optional[str] = None   # DeepSeek may omit for rejected jobs

class JobEvaluationReport(BaseModel):
    evaluated_jobs: List[EvaluatedJob]

def evaluate_and_draft(jobs: List[dict]) -> str:
    if not jobs:
        return "No valid jobs were extracted from the search results."

    print("🧠 Phase 3: DeepSeek V3 evaluating jobs & writing proposals...")
    
    llm = ChatOpenAI(
        model="deepseek-chat", 
        api_key=DEEPSEEK_API_KEY, 
        base_url="https://api.deepseek.com",
        max_tokens=4000, 
        temperature=0.2
    )
    
    parser = PydanticOutputParser(pydantic_object=JobEvaluationReport)

    prompt = ChatPromptTemplate.from_messages([
        ("system", """You are Utkarsh's AI Agent. Evaluate these job postings.
        Utkarsh's stack: AI, RAG, Python, LLMs (OpenAI, LLaMA, Gemini), Generative AI, NLP, PyTorch, CrewAI, TensorFlow, Transformers, AI Agents, Prompt Engineering, MLOps, MCP, LangChain, LangGraph, LiveKit, FastAPI, Deepgram, Pinecone, Voice AI.
        
        1. Set is_match to False if the job is irrelevant (e.g. requires Java, UI/UX, C++).
        2. If is_match is True, draft a highly technical 3-paragraph proposal highlighting his relevant metrics (e.g. reduced latency by 45%, handles 2000+ concurrent voice agents).
        
        CRITICAL INSTRUCTIONS:
        {format_instructions}"""),
        ("user", "Extracted Jobs JSON:\n{jobs}")
    ])

    chain = prompt | llm
    
    try:
        raw_response = chain.invoke({
            "jobs": json.dumps(jobs),
            "format_instructions": parser.get_format_instructions()
        })
        raw_text = raw_response.content
        # Strip markdown code fences if present
        if "```json" in raw_text:
            raw_text = raw_text.split("```json")[1].split("```")[0].strip()
        elif "```" in raw_text:
            raw_text = raw_text.split("```")[1].split("```")[0].strip()
        result = parser.parse(raw_text)
        return result.model_dump_json(indent=2)
    except Exception as e:
        print(f"⚠️ Parsing error in Phase 3: {e}")
        print("📄 Raw LLM response (use this if parsing failed):")
        try:
            print(raw_text)
        except NameError:
            pass
        return "{}"

# ==========================================
# MAIN ORCHESTRATOR (This is what you were missing!)
# ==========================================

async def main():
    print("\n🚀 STARTING AUTONOMOUS JOB SEARCH AGENT...\n")
    
    # 1. Search the web autonomously
    urls_to_scrape = search_for_jobs()
    
    if not urls_to_scrape:
        print("No URLs found today. Exiting.")
        return
        
    # 2. Extract data from unknown websites dynamically
    extracted_jobs = await extract_jobs_from_urls(urls_to_scrape)
    
    # 3. Analyze and draft
    final_report_json = evaluate_and_draft(extracted_jobs)
    
    print("\n" + "="*50)
    print("FINAL AGENT OUTPUT (READY FOR REVIEW)")
    print("="*50)
    print(final_report_json)

    # Save report to file for later review
    import datetime
    reports_dir = os.path.join(os.path.dirname(__file__), "reports")
    os.makedirs(reports_dir, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    report_path = os.path.join(reports_dir, f"{timestamp}_job_report.json")
    with open(report_path, "w") as f:
        f.write(final_report_json)
    print(f"\n💾 Report saved to: {report_path}")

if __name__ == "__main__":
    asyncio.run(main())