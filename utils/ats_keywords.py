"""
ATS Keywords Database - Industry-specific keywords for resume optimization
"""

# Action verbs categorized by impact level
ACTION_VERBS = {
    "leadership": [
        "Spearheaded", "Orchestrated", "Championed", "Pioneered", "Directed",
        "Led", "Managed", "Supervised", "Mentored", "Coached", "Guided",
        "Coordinated", "Oversaw", "Headed", "Steered"
    ],
    "achievement": [
        "Achieved", "Accomplished", "Exceeded", "Surpassed", "Delivered",
        "Attained", "Earned", "Won", "Secured", "Captured"
    ],
    "improvement": [
        "Improved", "Enhanced", "Optimized", "Streamlined", "Accelerated",
        "Increased", "Boosted", "Elevated", "Maximized", "Strengthened"
    ],
    "creation": [
        "Developed", "Created", "Designed", "Built", "Established",
        "Launched", "Initiated", "Introduced", "Implemented", "Executed"
    ],
    "analysis": [
        "Analyzed", "Evaluated", "Assessed", "Investigated", "Researched",
        "Identified", "Discovered", "Diagnosed", "Examined", "Audited"
    ],
    "collaboration": [
        "Collaborated", "Partnered", "Liaised", "Facilitated", "Negotiated",
        "Influenced", "Engaged", "Aligned", "Unified", "Integrated"
    ]
}

# Technical skills by industry
TECHNICAL_SKILLS = {
    "software_engineering": [
        "Python", "JavaScript", "TypeScript", "Java", "C++", "Go", "Rust",
        "React", "Node.js", "Django", "FastAPI", "AWS", "Azure", "GCP",
        "Docker", "Kubernetes", "CI/CD", "Git", "Agile", "Scrum",
        "REST API", "GraphQL", "Microservices", "SQL", "NoSQL", "MongoDB",
        "PostgreSQL", "Redis", "Machine Learning", "AI", "TensorFlow", "PyTorch"
    ],
    "data_science": [
        "Python", "R", "SQL", "Machine Learning", "Deep Learning", "NLP",
        "TensorFlow", "PyTorch", "Scikit-learn", "Pandas", "NumPy",
        "Data Visualization", "Tableau", "Power BI", "Statistical Analysis",
        "A/B Testing", "Feature Engineering", "Big Data", "Spark", "Hadoop"
    ],
    "product_management": [
        "Product Strategy", "Roadmapping", "User Research", "A/B Testing",
        "Agile", "Scrum", "JIRA", "Product Analytics", "KPIs", "OKRs",
        "Stakeholder Management", "Go-to-Market", "Product Launch",
        "User Stories", "Backlog Management", "Cross-functional Leadership"
    ],
    "marketing": [
        "Digital Marketing", "SEO", "SEM", "PPC", "Content Marketing",
        "Social Media Marketing", "Email Marketing", "Marketing Automation",
        "Google Analytics", "HubSpot", "Salesforce", "Brand Strategy",
        "Campaign Management", "Lead Generation", "Conversion Optimization"
    ],
    "finance": [
        "Financial Analysis", "Financial Modeling", "Budgeting", "Forecasting",
        "P&L Management", "Cost Optimization", "Risk Management", "Compliance",
        "Excel", "Bloomberg Terminal", "SAP", "QuickBooks", "GAAP", "IFRS"
    ],
    "healthcare": [
        "Patient Care", "Clinical Documentation", "HIPAA Compliance", "EMR/EHR",
        "Care Coordination", "Quality Improvement", "Evidence-Based Practice",
        "Medication Administration", "Patient Education", "Interdisciplinary Care"
    ]
}

# Soft skills that ATS systems look for
SOFT_SKILLS = [
    "Communication", "Leadership", "Problem-Solving", "Critical Thinking",
    "Teamwork", "Collaboration", "Adaptability", "Time Management",
    "Project Management", "Strategic Planning", "Decision Making",
    "Conflict Resolution", "Emotional Intelligence", "Creativity",
    "Attention to Detail", "Self-Motivated", "Results-Driven"
]

# Metrics and quantification templates
METRICS_TEMPLATES = [
    "Increased {metric} by {percentage}%",
    "Reduced {metric} by {percentage}%",
    "Managed a team of {number} professionals",
    "Delivered ${amount} in revenue/savings",
    "Completed {number} projects on time and under budget",
    "Achieved {percentage}% customer satisfaction rating",
    "Processed {number}+ transactions/requests monthly",
    "Grew user base from {start} to {end} ({percentage}% increase)"
]

# Professional certifications and their variations
CERTIFICATIONS = {
    "technology": [
        "AWS Certified Solutions Architect", "AWS CSA",
        "Google Cloud Professional", "GCP",
        "Microsoft Certified Azure", "Azure Certified",
        "PMP (Project Management Professional)", "Project Management Professional",
        "CISSP (Certified Information Systems Security Professional)",
        "Certified Scrum Master", "CSM", "Scrum Master",
        "Kubernetes Certified", "CKA", "CKAD"
    ],
    "data": [
        "Certified Data Scientist", "Data Science Certification",
        "Google Data Analytics Certificate",
        "IBM Data Science Professional",
        "Tableau Desktop Specialist"
    ],
    "business": [
        "MBA (Master of Business Administration)", "Master of Business Administration",
        "Six Sigma Black Belt", "Lean Six Sigma",
        "Certified Public Accountant", "CPA",
        "Chartered Financial Analyst", "CFA"
    ]
}

# Skill synonyms for semantic matching
SKILL_SYNONYMS = {
    "Machine Learning": ["ML", "Machine Learning", "Machine Learning Models", "ML Models"],
    "Artificial Intelligence": ["AI", "Artificial Intelligence", "AI/ML"],
    "Project Management": ["Project Management", "Program Management", "Managing Projects"],
    "Software Development": ["Software Development", "Software Engineering", "Application Development"],
    "Data Analysis": ["Data Analysis", "Data Analytics", "Analytical Skills"],
    "Leadership": ["Leadership", "Team Leadership", "Leading Teams", "Management"],
    "Communication": ["Communication", "Communication Skills", "Interpersonal Skills"],
    "Cloud Computing": ["Cloud Computing", "Cloud Technologies", "Cloud Infrastructure"],
    "DevOps": ["DevOps", "CI/CD", "Continuous Integration", "Continuous Deployment"],
    "Agile": ["Agile", "Agile Methodologies", "Scrum", "Kanban"]
}


def get_industry_keywords(industry: str) -> list:
    """Get relevant technical keywords for a specific industry."""
    industry_key = industry.lower().replace(" ", "_")
    return TECHNICAL_SKILLS.get(industry_key, TECHNICAL_SKILLS["software_engineering"])


def get_all_action_verbs() -> list:
    """Get all action verbs as a flat list."""
    all_verbs = []
    for category_verbs in ACTION_VERBS.values():
        all_verbs.extend(category_verbs)
    return all_verbs


def extract_keywords_from_job_description(job_description: str) -> dict:
    """
    Extract potential keywords from a job description.
    
    Returns dict with categorized keywords found.
    """
    job_desc_lower = job_description.lower()
    found_keywords = {
        "technical_skills": [],
        "soft_skills": [],
        "action_verbs": []
    }
    
    # Check all technical skills across industries
    for industry, skills in TECHNICAL_SKILLS.items():
        for skill in skills:
            if skill.lower() in job_desc_lower:
                if skill not in found_keywords["technical_skills"]:
                    found_keywords["technical_skills"].append(skill)
    
    # Check soft skills
    for skill in SOFT_SKILLS:
        if skill.lower() in job_desc_lower:
            found_keywords["soft_skills"].append(skill)
    
    return found_keywords
