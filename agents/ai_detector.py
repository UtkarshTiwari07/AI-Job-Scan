"""
AI Content Detector Agent - Identifies AI-generated patterns in resume sections
Rewritten for precise section-level detection with confidence scores
"""
from crewai import Agent, Task
import json


def create_ai_detector_agent(llm) -> Agent:
    """
    Create the AI Content Detector Agent.
    Returns JSON with flagged sections and confidence scores.
    """
    return Agent(
        role="AI Content Detection Specialist",
        goal="Identify resume sections with >30% AI probability and output structured JSON",
        backstory="""You are an expert in detecting AI-generated text patterns. You've analyzed 
        thousands of resumes and can instantly spot generic AI phrases, repetitive patterns, and 
        unnatural phrasing. You provide precise confidence scores for each section.""",
        llm=llm,
        verbose=True,
        allow_delegation=False
    )


def create_ai_detection_task(agent: Agent, resume: str) -> Task:
    """Create the AI detection task with section-level analysis."""
    
    description = f"""ANALYZE this resume for AI-generated content patterns. You are an expert AI detector with 5+ years of experience. Output JSON with flagged sections.

RESUME TO ANALYZE:
{resume}

DETECTION CRITERIA (Weighted by Signal Strength):

═══════════════════════════════════════════════════════════════
1. LOW PERPLEXITY PATTERNS (VERY HIGH SIGNAL - 40% weight)
═══════════════════════════════════════════════════════════════
AI generates text where EVERY word is the most predictable next word.
- AI Example: "I am a highly motivated professional with extensive experience in delivering results"
- Human Example: "After 8 years in fintech, I've shipped 3 products from scratch"

FLAG IF: Sentences flow too smoothly with no unexpected word choices.

═══════════════════════════════════════════════════════════════
2. LACK OF BURSTINESS (HIGH SIGNAL - 25% weight)
═══════════════════════════════════════════════════════════════
AI writes sentences of similar length (15-20 words consistently).
Human writing VARIES wildly: some 5-word punchy sentences, some 30+ word explanations.

FLAG IF: All bullets/sentences are within ±5 words of each other.

═══════════════════════════════════════════════════════════════
3. AI TRAINING DATA ARTIFACTS (HIGH SIGNAL - 20% weight)
═══════════════════════════════════════════════════════════════
These EXACT phrases appear 100x more in AI text than human text:
- "Spearheaded initiatives"
- "Drove strategic outcomes" 
- "Leveraged expertise to deliver"
- "Passionate about [anything]"
- "Proven ability to"
- "Successfully managed"
- "Cross-functional collaboration"
- "Strong track record"
- "Demonstrated ability"
- "Key stakeholders"
- "Best practices"
- "End-to-end"
- "Cutting-edge"
- "State-of-the-art"
- "Actionable insights"

FLAG IMMEDIATELY if ANY of these appear.

═══════════════════════════════════════════════════════════════
4. PARALLEL STRUCTURE OVERUSE (MEDIUM SIGNAL - 10% weight)
═══════════════════════════════════════════════════════════════
AI loves repeating: "Verb + Object + Metric" pattern.
- AI: "Developed X achieving 20%", "Developed Y achieving 30%", "Developed Z achieving 40%"
- Human: Naturally varies structure without thinking about it

FLAG IF: 3+ consecutive bullets start with the same word or structure.

═══════════════════════════════════════════════════════════════
5. GENERIC EUPHEMISMS WITHOUT SPECIFICS (MEDIUM SIGNAL - 5% weight)
═══════════════════════════════════════════════════════════════
- "Proven track record" → should be specific achievement
- "Results-driven professional" → what specific results?
- "Detail-oriented team player" → show, don't tell
- "Exceeded expectations" → whose expectations? by how much?

YOUR TASK:
1. Split resume into sections (Summary, Each Job, Skills, Education)
2. Analyze EACH section using the weighted criteria above
3. Calculate AI probability (0-100%) for each section
4. Flag sections >30% as needing humanization
5. INCLUDE THE EXACT TEXT of flagged sections

OUTPUT FORMAT (STRICT JSON - NO OTHER TEXT):
{{
  "overall_ai_score": 45,
  "flagged_sections": [
    {{
      "section_name": "Professional Summary",
      "content": "[EXACT text from resume - copy it completely]",
      "ai_probability": 65,
      "issues": [
        "Training artifact: 'spearheaded initiatives'",
        "Low burstiness: all sentences 15-18 words",
        "Parallel structure: 3 bullets start with 'Developed'"
      ]
    }}
  ],
  "clean_sections": [
    {{
      "section_name": "Education",
      "ai_probability": 10
    }}
  ]
}}

RULES:
- ONLY include sections with ai_probability > 30 in flagged_sections
- COPY the EXACT section content (needed for humanization)
- Be PRECISE and HARSH with scoring - if it sounds AI, flag it
- List SPECIFIC issues with exact phrases found

Output ONLY valid JSON, no explanation text."""

    return Task(
        description=description,
        agent=agent,
        expected_output="JSON object with flagged sections, confidence scores, and specific issues identified"
    )
