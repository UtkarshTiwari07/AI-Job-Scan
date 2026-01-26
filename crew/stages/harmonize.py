# Stage H: Harmonize / Analyze
# Detects domain, tone, register, and extracts factual claims

"""
HARMONIZE STAGE - First step in HUMANIZE pipeline
Purpose: Analyze text to determine processing strategy
"""

import re
from typing import Dict, List, Tuple


class HarmonizeStage:
    """
    Analyze text before humanization to:
    1. Detect domain (resume, article, email)
    2. Detect register (formal, informal, technical)
    3. Detect tone (professional, casual, academic)
    4. Extract claims that MUST be preserved
    """
    
    # Domain detection signals
    RESUME_SIGNALS = [
        "experience", "skills", "education", "summary", "professional",
        "responsibilities", "achievements", "certifications", "projects"
    ]
    
    # Register markers
    FORMAL_MARKERS = [
        "furthermore", "consequently", "demonstrate", "utilize", "facilitate",
        "regarding", "upon", "hereby", "therefore", "henceforth"
    ]
    
    INFORMAL_MARKERS = [
        "gonna", "wanna", "kinda", "sort of", "pretty much", "like",
        "basically", "honestly", "actually"
    ]
    
    TECHNICAL_MARKERS = [
        "api", "algorithm", "implementation", "architecture", "database",
        "framework", "latency", "throughput", "scalability", "deployment"
    ]
    
    def analyze(self, text: str) -> Dict:
        """
        Main analysis method - returns complete text profile.
        
        Returns:
            dict with: domain, register, tone, claims, word_count, sentence_count
        """
        return {
            "domain": self._detect_domain(text),
            "register": self._detect_register(text),
            "tone": self._detect_tone(text),
            "claims": self._extract_claims(text),
            "word_count": len(text.split()),
            "sentence_count": len(re.split(r'[.!?]+', text))
        }
    
    def _detect_domain(self, text: str) -> str:
        """Detect the document domain."""
        text_lower = text.lower()
        
        # Check for resume signals
        resume_score = sum(1 for signal in self.RESUME_SIGNALS if signal in text_lower)
        if resume_score >= 3:
            return "resume"
        
        # Check for email signals
        email_signals = ["dear", "regards", "sincerely", "best,", "thanks,"]
        if any(signal in text_lower for signal in email_signals):
            return "email"
        
        # Check for article signals
        article_signals = ["introduction", "conclusion", "abstract", "references"]
        if any(signal in text_lower for signal in article_signals):
            return "article"
        
        return "general"
    
    def _detect_register(self, text: str) -> str:
        """Detect formality register: formal, informal, or technical."""
        text_lower = text.lower()
        
        formal_count = sum(1 for m in self.FORMAL_MARKERS if m in text_lower)
        informal_count = sum(1 for m in self.INFORMAL_MARKERS if m in text_lower)
        technical_count = sum(1 for m in self.TECHNICAL_MARKERS if m in text_lower)
        
        # Technical takes precedence for resume domain
        if technical_count >= 3:
            return "technical"
        
        if formal_count > informal_count:
            return "formal"
        elif informal_count > formal_count:
            return "informal"
        
        return "neutral"
    
    def _detect_tone(self, text: str) -> str:
        """Detect emotional/professional tone."""
        text_lower = text.lower()
        
        # Professional indicators
        professional_signals = [
            "managed", "led", "developed", "implemented", "achieved",
            "delivered", "optimized", "designed", "built"
        ]
        
        # Casual indicators
        casual_signals = [
            "cool", "awesome", "great", "nice", "pretty good", "not bad"
        ]
        
        professional_score = sum(1 for s in professional_signals if s in text_lower)
        casual_score = sum(1 for s in casual_signals if s in text_lower)
        
        if professional_score > casual_score:
            return "professional"
        elif casual_score > professional_score:
            return "casual"
        
        return "neutral"
    
    def _extract_claims(self, text: str) -> List[str]:
        """
        Extract factual claims that MUST be preserved during humanization.
        These include: dates, metrics, company names, percentages, monetary values.
        """
        claims = []
        
        # Date patterns: Jan 2020, 2020-2025, June 2025 – Present
        date_patterns = [
            r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}\b',
            r'\b\d{4}\s*[-–]\s*(Present|\d{4})\b',
            r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b'
        ]
        for pattern in date_patterns:
            claims.extend(re.findall(pattern, text, re.IGNORECASE))
        
        # Percentage patterns: 95%, 90-95%
        percentage_pattern = r'\b\d+(?:\.\d+)?%'
        claims.extend(re.findall(percentage_pattern, text))
        
        # Monetary values: $2M, $500K, $1.5 million
        money_pattern = r'\$[\d,]+(?:\.\d+)?[KMB]?(?:\s*(?:million|billion|thousand))?'
        claims.extend(re.findall(money_pattern, text, re.IGNORECASE))
        
        # Numeric metrics: 500 users, 2,000+ calls, 43 indexes
        metric_pattern = r'\b[\d,]+\+?\s*(?:users?|calls?|clients?|customers?|employees?|engineers?|indexes|endpoints?|APIs?)\b'
        claims.extend(re.findall(metric_pattern, text, re.IGNORECASE))
        
        # Company names (capitalized multi-word names)
        # Simple heuristic: words that appear with | separator in job entries
        company_pattern = r'([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*)\s*\|'
        claims.extend(re.findall(company_pattern, text))
        
        # Remove duplicates while preserving order
        seen = set()
        unique_claims = []
        for claim in claims:
            claim_str = str(claim) if not isinstance(claim, str) else claim
            if claim_str.lower() not in seen:
                seen.add(claim_str.lower())
                unique_claims.append(claim_str)
        
        return unique_claims
