"""
ATS Scoring Agent - Calculates resume match percentage against job description
Based on professional ATS algorithms (Taleo, Workday)
"""
from crewai import Agent, Task
from typing import Dict, List
import re
from collections import Counter


def create_ats_scorer_agent(llm) -> Agent:
    """
    Create the ATS Scoring Agent.
    
    Calculates precise ATS match score using:
    - 40% Keyword matching
    - 30% Skills alignment
    - 20% Experience relevance
    - 10% Education match
    """
    return Agent(
        role="ATS Resume Scoring Specialist",
        goal="Calculate precise ATS match percentage (target: 80%+) and identify missing keywords",
        backstory="""You are an expert in Applicant Tracking Systems used by Fortune 500 companies 
        (Taleo, Workday, Greenhouse). You understand exactly how these systems parse, score, and rank 
        resumes. Your scoring must be precise and match industry standards.""",
        llm=llm,
        verbose=True,
        allow_delegation=False
    )


def create_ats_scoring_task(agent: Agent, resume: str, job_description: str) -> Task:
    """Create the ATS scoring task with professional prompt."""
    
    description = f"""ANALYZE this resume against the job description and calculate ATS match score.

JOB DESCRIPTION:
{job_description}

RESUME:
{resume}

YOUR TASK:
1. Extract ALL required keywords from job description:
   - Technical skills (exact terms)
   - Soft skills
   - Certifications/degrees
   - Job title keywords
   - Industry terminology
   - Years of experience

2. Calculate match scores:
   a) KEYWORD MATCH (40% weight):
      - Count exact keyword matches
      - Check for synonyms/variations
      - Calculate: (matched_keywords / total_keywords) * 100
   
   b) SKILLS ALIGNMENT (30% weight):
      - Required skills present: ___%
      - Preferred skills present: ___%
      - Calculate weighted average
   
   c) EXPERIENCE RELEVANCE (20% weight):
      - Years of experience match: Yes/No
      - Relevant job titles: Yes/No
      - Industry experience: Yes/No
   
   d) EDUCATION MATCH (10% weight):
      - Degree level matches requirement: Yes/No
      - Relevant field of study: Yes/No

3. Calculate TOTAL ATS SCORE:
   Total = (keyword_score * 0.40) + (skills_score * 0.30) + (experience_score * 0.20) + (education_score * 0.10)

OUTPUT FORMAT (EXACTLY):
ATS SCORE: [number]%

KEYWORD MATCH: [number]%
SKILLS MATCH: [number]%
EXPERIENCE MATCH: [number]%
EDUCATION MATCH: [number]%

MISSING CRITICAL KEYWORDS:
- [keyword 1]
- [keyword 2]
- [keyword 3]
...

RECOMMENDATIONS:
1. [specific improvement]
2. [specific improvement]
3. [specific improvement]

Be PRECISE with percentages. Round to nearest whole number."""

    return Task(
        description=description,
        agent=agent,
        expected_output="ATS score percentage with detailed breakdown and missing keywords list"
    )


# Utility function for keyword extraction
def extract_keywords(text: str) -> List[str]:
    """Extract potential keywords from text (skills, technologies, certifications)."""
    # Common skill/tech patterns
    patterns = [
        r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b',  # Proper nouns
        r'\b[A-Z]{2,}\b',  # Acronyms
        r'\b\d+\+?\s*years?\b',  # Experience years
        r'\b(?:Bachelor|Master|PhD|MBA|B\.S\.|M\.S\.|Ph\.D\.)\b',  # Degrees
    ]
    
    keywords = set()
    for pattern in patterns:
        matches = re.findall(pattern, text)
        keywords.update(matches)
    
    return list(keywords)


def calculate_keyword_match(resume_keywords: List[str], jd_keywords: List[str]) -> float:
    """Calculate keyword match percentage."""
    if not jd_keywords:
        return 100.0
    
    resume_set = set(k.lower() for k in resume_keywords)
    jd_set = set(k.lower() for k in jd_keywords)
    
    matches = len(resume_set.intersection(jd_set))
    total = len(jd_set)
    
    return round((matches / total) * 100, 1) if total > 0 else 0.0


def get_missing_keywords(resume_keywords: List[str], jd_keywords: List[str]) -> List[str]:
    """Identify keywords present in JD but missing from resume."""
    resume_set = set(k.lower() for k in resume_keywords)
    jd_set = set(k.lower() for k in jd_keywords)
    
    missing = jd_set - resume_set
    return sorted(list(missing))
