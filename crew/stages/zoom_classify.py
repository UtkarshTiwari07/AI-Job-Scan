# Stage Z: Zoom-out Classifier
# Human-likeness scoring with explainability

"""
ZOOM CLASSIFY STAGE - Seventh step in HUMANIZE pipeline
Purpose: Score human-likeness and provide explainability for any issues
"""

import re
from typing import Dict, List, Tuple


class ZoomClassifyStage:
    """
    Human-likeness scoring with explainability.
    
    This is a rule-based classifier that mimics what AI detectors look for.
    Provides a score and explains why text might be flagged.
    """
    
    # AI fingerprint words (detectors look for these)
    AI_FINGERPRINT_WORDS = [
        "leverage", "utilize", "spearhead", "orchestrate", "facilitate",
        "comprehensive", "robust", "scalable", "synergy", "paradigm",
        "holistic", "cutting-edge", "state-of-the-art", "innovative",
        "passionate", "exceptional", "unwavering", "multifaceted",
        "dynamic", "proactive", "streamline", "optimize", "delve",
        "tapestry", "landscape", "testament", "pivotal", "paramount"
    ]
    
    # Common AI transition phrases
    AI_TRANSITIONS = [
        "furthermore", "moreover", "additionally", "consequently",
        "subsequently", "in addition", "as a result", "thus",
        "hence", "therefore", "accordingly"
    ]
    
    # Penalty weights
    WEIGHTS = {
        "ai_word": 5,           # Per AI fingerprint word
        "low_burstiness": 15,   # If CV < 0.25
        "no_contractions": 10,  # If no contractions in 100+ words
        "parallel_structure": 8, # If 4+ bullets start same way
        "ai_transition": 3,     # Per AI transition phrase
        "uniform_length": 10,   # All sentences within 5 words of average
    }
    
    def score(self, text: str) -> Dict:
        """
        Calculate human-likeness score with detailed explanation.
        
        Returns:
            Dict with human_score, issues, passed, and explanation
        """
        score = 100  # Start at 100, subtract for issues
        issues = []
        details = {}
        
        # 1. Check for AI fingerprint words
        ai_words_found = self._find_ai_words(text)
        if ai_words_found:
            penalty = len(ai_words_found) * self.WEIGHTS["ai_word"]
            score -= penalty
            issues.append(f"AI words found: {', '.join(ai_words_found[:5])}")
            details["ai_words"] = ai_words_found
        
        # 2. Check burstiness (sentence length variance)
        cv = self._calculate_cv(text)
        details["burstiness_cv"] = cv
        if cv < 0.25:
            score -= self.WEIGHTS["low_burstiness"]
            issues.append(f"Low burstiness (CV={cv:.2f}, need >0.25)")
        elif cv < 0.30:
            score -= self.WEIGHTS["low_burstiness"] // 2
            issues.append(f"Marginal burstiness (CV={cv:.2f})")
        
        # 3. Check for contractions
        contraction_count, word_count = self._count_contractions(text)
        details["contractions"] = contraction_count
        details["word_count"] = word_count
        if word_count > 100 and contraction_count < 2:
            score -= self.WEIGHTS["no_contractions"]
            issues.append("Few/no contractions (humans use them)")
        
        # 4. Check parallel structure in bullets
        parallel_issues = self._check_parallel_structure(text)
        if parallel_issues:
            score -= self.WEIGHTS["parallel_structure"]
            issues.append(parallel_issues)
        
        # 5. Check for AI transitions
        ai_transitions = self._find_ai_transitions(text)
        if ai_transitions:
            penalty = min(len(ai_transitions) * self.WEIGHTS["ai_transition"], 15)
            score -= penalty
            issues.append(f"AI transitions: {', '.join(ai_transitions[:3])}")
        
        # 6. Check sentence length uniformity
        is_uniform, avg_len = self._check_uniform_length(text)
        if is_uniform:
            score -= self.WEIGHTS["uniform_length"]
            issues.append(f"Uniform sentence lengths (~{avg_len} words each)")
        
        # Ensure score is in valid range
        score = max(0, min(100, score))
        
        return {
            "human_score": score,
            "issues": issues,
            "passed": score >= 75,
            "details": details,
            "explanation": self._generate_explanation(score, issues),
            "recommendation": self._get_recommendation(score, issues)
        }
    
    def _find_ai_words(self, text: str) -> List[str]:
        """Find AI fingerprint words in text."""
        text_lower = text.lower()
        found = []
        for word in self.AI_FINGERPRINT_WORDS:
            if word in text_lower:
                found.append(word)
        return found
    
    def _find_ai_transitions(self, text: str) -> List[str]:
        """Find AI transition phrases."""
        text_lower = text.lower()
        found = []
        for phrase in self.AI_TRANSITIONS:
            if phrase in text_lower:
                found.append(phrase)
        return found
    
    def _calculate_cv(self, text: str) -> float:
        """Calculate coefficient of variation for sentence lengths."""
        sentences = re.split(r'(?<=[.!?])\s+', text)
        lengths = [len(s.split()) for s in sentences if len(s.split()) > 0]
        
        if len(lengths) < 3:
            return 0.5  # Default for short texts
        
        avg = sum(lengths) / len(lengths)
        if avg == 0:
            return 0.0
        
        std = (sum((l - avg)**2 for l in lengths) / len(lengths)) ** 0.5
        return round(std / avg, 3)
    
    def _count_contractions(self, text: str) -> Tuple[int, int]:
        """Count contractions in text."""
        contractions = re.findall(r"\b\w+'\w+\b", text)
        words = len(text.split())
        return len(contractions), words
    
    def _check_parallel_structure(self, text: str) -> str:
        """Check for repetitive bullet point starts."""
        lines = text.split('\n')
        bullet_starts = {}
        
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('•') or stripped.startswith('-'):
                words = stripped.lstrip('•- ').split()
                if words:
                    first = words[0]
                    bullet_starts[first] = bullet_starts.get(first, 0) + 1
        
        # Find most repeated start
        if bullet_starts:
            max_word, max_count = max(bullet_starts.items(), key=lambda x: x[1])
            if max_count >= 4:
                return f"Parallel structure: {max_count} bullets start with '{max_word}'"
        
        return ""
    
    def _check_uniform_length(self, text: str) -> Tuple[bool, int]:
        """Check if all sentences are similar length (AI pattern)."""
        sentences = re.split(r'(?<=[.!?])\s+', text)
        lengths = [len(s.split()) for s in sentences if len(s.split()) > 3]
        
        if len(lengths) < 4:
            return False, 0
        
        avg = sum(lengths) / len(lengths)
        
        # Check if all within 5 words of average
        all_uniform = all(abs(l - avg) <= 5 for l in lengths)
        
        return all_uniform, int(avg)
    
    def _generate_explanation(self, score: int, issues: List[str]) -> str:
        """Generate human-readable explanation."""
        if score >= 90:
            return "Excellent! Text appears highly human-written."
        elif score >= 75:
            return f"Good. Minor issues detected: {len(issues)} possible AI signals."
        elif score >= 60:
            return f"Moderate. Text has {len(issues)} AI patterns that may be detected."
        else:
            return f"Warning. Text has significant AI patterns. {len(issues)} issues found."
    
    def _get_recommendation(self, score: int, issues: List[str]) -> str:
        """Get actionable recommendation."""
        if score >= 85:
            return "Ready for use."
        elif score >= 70:
            return "Consider light editing to address noted issues."
        elif score >= 50:
            return "Recommend re-running humanization with higher variability."
        else:
            return "Strongly recommend complete rewrite with different approach."
