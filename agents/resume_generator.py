"""
Resume Generator Agent - Creates ATS-optimized resumes
Rewritten for 80%+ ATS match and keyword optimization
"""
from crewai import Agent, Task


def create_resume_generator_agent(llm) -> Agent:
    """
    Create the Resume Generator Agent with professional ATS optimization focus.
    """
    return Agent(
        role="ATS Resume Optimization Specialist",
        goal="Generate resume with 80%+ ATS match score using exact job description keywords",
        backstory="""You are an expert resume writer specializing in ATS optimization for Fortune 500 
        companies. You know exactly how Taleo, Workday, and Greenhouse systems score resumes. You NEVER 
        use generic phrases - only specific, quantified achievements with exact keywords from the job posting.""",
        llm=llm,
        verbose=True,
        allow_delegation=False
    )


def create_resume_generation_task(
    agent: Agent,
    original_resume: str,
    job_description: str,
    user_instructions: str = ""
) -> Task:
    """Create the resume generation task with optimized prompt."""
    
    description = f"""CREATE an ATS-optimized resume that scores 80%+ match with the job description.

JOB DESCRIPTION:
{job_description}

ORIGINAL RESUME:
{original_resume}

USER INSTRUCTIONS:
{user_instructions if user_instructions else "None"}

REQUIREMENTS:
1. KEYWORD MATCHING (Critical):
   - Extract EVERY skill, technology, certification, and qualification from job description
   - Use EXACT phrases (e.g., if JD says "project management", use "project management" not "managed projects")
   - Include both long form and acronyms (e.g., "Machine Learning (ML)")
   - Mirror job title terminology exactly

2. FACT PRESERVATION (CRITICAL - DO NOT VIOLATE):
   - COPY EXACTLY from original: company names, job titles, dates, metrics
   - NEVER fabricate numbers, percentages, or achievements not in original
   - NEVER add experiences not in original resume
   - NEVER change years of experience or date ranges
   - You may ONLY add keywords from job description to describe EXISTING achievements
   - If original lacks metrics, DO NOT invent them - use qualitative descriptions instead

3. ACTION VERBS (Start every bullet):
   - Use strong action verbs: Developed, Engineered, Architected, Led, Optimized, Spearheaded
   - NEVER passive voice or weak verbs like "Responsible for" or "Worked on"

4. STRUCTURE (ATS-Friendly):
   - Use standard headers: PROFESSIONAL SUMMARY, EXPERIENCE, SKILLS, EDUCATION
   - Reverse chronological order
   - Job titles exactly match or closely align with target role
   - No tables, columns, or graphics

5. PROFESSIONAL SUMMARY:
   - 3-4 lines maximum
   - Include: years of experience + key skills from JD + major achievement
   - Example: "Senior Software Engineer with 8+ years developing scalable cloud applications using AWS, Python, and Kubernetes. Led cross-functional teams delivering $5M+ projects. Expert in CI/CD, microservices architecture, and agile methodologies."

6. SKILLS SECTION:
   - Group by category: Technical Skills, Tools & Technologies, Methodologies
   - List EXACT skills from job description first
   - Include proficiency levels if relevant

OUTPUT FORMAT:
[FULL NAME]
[Email] | [Phone] | [LinkedIn] | [Location]

PROFESSIONAL SUMMARY
[3-4 lines with keywords and quantified achievement]

EXPERIENCE
[Company Name] | [Job Title] | [Dates]
• [Achievement with metrics and keywords]
• [Achievement with metrics and keywords]
• [Achievement with metrics and keywords]

SKILLS
Technical Skills: [comma-separated list]
Tools & Technologies: [comma-separated list]

EDUCATION
[Degree] in [Field] | [University] | [Year]

CONSTRAINTS:
- NO generic buzzwords without context
- NO passive voice
- NO achievements without metrics
- NO creative section names
- MUST use keywords from job description naturally throughout

ANTI-AI PATTERN RULES (CRITICAL - Apply these to sound human):
1. NEVER use these AI-giveaway phrases:
   - "Spearheaded", "Leveraged", "Drove strategic", "Proven track record"
   - "Results-driven", "Cross-functional collaboration", "Key stakeholders"
   - "Passionate about", "Best practices", "Demonstrated ability"
   
2. VARY sentence length deliberately:
   - Include at least 2 short punchy bullets (5-8 words): "Cut costs by 40%."
   - Include at least 2 longer detailed bullets (20+ words)
   
3. USE contractions naturally: "didn't", "won't", "I've", "we'd"

4. BREAK parallel structure:
   - DON'T start 3+ bullets with the same verb
   - Mix up: "Built X", "My role was Y", "Responsible for Z", "Led the team on W"

5. START some bullets with numbers: "12 engineers reported to me", "3 major releases shipped"

Generate the complete resume now."""

    return Task(
        description=description,
        agent=agent,
        expected_output="A complete, ATS-optimized resume with exact keyword matches and quantified achievements"
    )
