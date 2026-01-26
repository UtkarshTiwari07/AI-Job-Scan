"""
NeuroHumanizer Agent - Professional Anti-Smoothing
Defeats AI detection while maintaining resume formality
"""
from crewai import Agent, Task


def create_humanizer_agent(llm) -> Agent:
    """
    Create the NeuroHumanizer Agent.
    Defeats AI detection while keeping professional resume tone.
    """
    return Agent(
        role="Executive Resume Writer & AI Detection Specialist",
        goal="Transform AI-generated resumes into undetectable, highly professional documents",
        backstory="""You are a senior executive resume writer with 15 years of experience at top firms.
        You also understand AI detection deeply - you know how detectors measure perplexity and burstiness.
        
        Your specialty: Write resumes that are IMPECCABLY PROFESSIONAL yet completely undetectable as AI.
        
        You achieve this by:
        - Using strong, varied action verbs (not the same ones AI overuses)
        - Varying sentence lengths naturally (short punchy results, longer context sentences)
        - Avoiding AI-favorite words while keeping formal business language
        - Writing like a polished human professional, never casual or sloppy
        
        Your resumes get interviews. They pass AI detection. They impress executives.""",
        llm=llm,
        verbose=True,
        allow_delegation=False
    )


def create_humanization_task(
    agent: Agent, 
    flagged_sections: list,
    full_resume_context: str = ""
) -> Task:
    """Create the professional anti-AI task."""
    
    sections_text = "\n\n".join([
        f"SECTION: {section['section_name']}\n"
        f"CONTENT:\n{section['content']}"
        for section in flagged_sections
    ])
    
    description = f"""Transform this resume to defeat AI detection while maintaining IMPECCABLE PROFESSIONALISM.

═══════════════════════════════════════════════════════════════
THE BALANCE: PROFESSIONAL + UNDETECTABLE
═══════════════════════════════════════════════════════════════

AI detectors flag text that is:
1. Too predictable (low perplexity) - AI always picks the "safest" word
2. Too uniform (low burstiness) - AI writes same-length sentences

BUT we must stay PROFESSIONAL. No casual language. This is a formal business document.

═══════════════════════════════════════════════════════════════
PROFESSIONAL ANTI-SMOOTHING RULES
═══════════════════════════════════════════════════════════════

RULE 1: STRONG ACTION VERBS (Professional, but varied)
Use powerful action verbs, but VARY them - don't repeat:

GOOD (Professional & Varied):
- Built, Designed, Engineered, Created, Developed
- Led, Directed, Managed, Headed, Oversaw
- Achieved, Delivered, Produced, Generated, Attained
- Reduced, Cut, Decreased, Lowered, Minimized
- Improved, Enhanced, Strengthened, Boosted, Elevated

BANNED (AI overuses these):
- Spearheaded, Leveraged, Utilized, Orchestrated
- Facilitated, Streamlined, Optimized (use "improved" instead)

RULE 2: SENTENCE LENGTH VARIATION (Professional burstiness)
Mix sentence lengths naturally:
- Short result statements: "Reduced latency by 45%." "Cut query time 90%."
- Longer context sentences: "This required integrating three separate APIs and designing a custom caching layer to handle concurrent requests."

RULE 3: STAY FORMAL - NO CASUAL LANGUAGE
NEVER use these casual phrases:
- "Pretty cool", "Totally my jam", "Nailed it"
- "Honestly", "Actually", "Basically"  
- "A lot of work", "Worth it though"
- "Wasn't terrible", "Pretty neat"
- Rhetorical questions like "Right?"

ALWAYS use formal business language:
- "Significant improvement" not "way better"
- "Substantial reduction" not "huge drop"
- "Successfully integrated" not "got it working"

RULE 4: PROFESSIONAL FIRST-PERSON (Limited)
Resumes typically use implied first-person (no "I"):
- "Built AI voice infrastructure" (not "I built...")
- "Led team of 5 engineers" (not "I led...")

Only use "I" in the summary if it fits naturally.

RULE 5: BANNED AI FINGERPRINT WORDS
Never use: leverage, utilize, spearhead, orchestrate, streamline,
comprehensive, robust, scalable, synergy, paradigm, holistic,
cutting-edge, state-of-the-art, passionate, exceptional, unwavering

RULE 6: PRESERVE ALL FACTS
Do NOT change:
- Company names, job titles, dates
- Metrics and numbers
- Technical terms and tools
- Core achievements

═══════════════════════════════════════════════════════════════
INPUT TO TRANSFORM
═══════════════════════════════════════════════════════════════

{sections_text}

CONTEXT:
{full_resume_context}

═══════════════════════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════════════════════

Output ONLY the complete, professional resume. No markers, no explanations.

FORMAT:
Name
Contact Info

PROFESSIONAL SUMMARY
[2-3 sentences. Formal. Varied lengths. Strong credentials.]

EXPERIENCE
Company | Title | Dates
• [Strong action verb] [achievement] [metric if available]
• [Varied verb] [different structure] [result]
• [Short punchy result statement.]

SKILLS
[Organized skill categories]

EDUCATION
[Education details]

Write the resume now. Make it professional, polished, and undetectable."""

    return Task(
        description=description,
        agent=agent,
        expected_output="Professional resume that defeats AI detection while maintaining formal business language"
    )
