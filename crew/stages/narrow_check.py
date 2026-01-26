# Stage N: Narrow & Check Facts
# Semantic similarity verification and claim preservation check

"""
NARROW CHECK STAGE - Fifth step in HUMANIZE pipeline
Purpose: Verify semantic fidelity and that facts weren't hallucinated
"""

import re
from typing import Dict, List, Any


class NarrowCheckStage:
    """
    Verify that humanization preserved meaning and facts.
    
    Checks:
    1. Overall semantic similarity (SBERT cosine)
    2. Claim preservation (dates, metrics, names)
    3. Length ratio (didn't lose or add too much content)
    """
    
    # Thresholds
    SIMILARITY_THRESHOLD = 0.75  # Minimum cosine similarity
    LENGTH_RATIO_MIN = 0.7  # Can't lose more than 30% of words
    LENGTH_RATIO_MAX = 1.5  # Can't add more than 50% words
    
    def __init__(self):
        self.unpack_stage = None  # Will use existing instance
    
    def check(self, original: str, rewritten: str,
              original_embeddings: Dict[str, Any],
              claims: List[str]) -> Dict:
        """
        Run all verification checks.
        
        Args:
            original: Original text
            rewritten: Humanized text
            original_embeddings: Embeddings from Unpack stage
            claims: List of claims that must be preserved
            
        Returns:
            Dictionary with check results and actions
        """
        results = {
            "passed": True,
            "checks": {},
            "issues": [],
            "action": "accept"
        }
        
        # 1. Semantic similarity check
        similarity = self._check_semantic_similarity(
            original, rewritten, original_embeddings
        )
        results["checks"]["semantic_similarity"] = similarity
        
        if similarity < self.SIMILARITY_THRESHOLD:
            results["passed"] = False
            results["issues"].append(
                f"Semantic drift detected (similarity={similarity:.2f}, threshold={self.SIMILARITY_THRESHOLD})"
            )
        
        # 2. Claim preservation check
        claims_result = self._verify_claims(rewritten, claims)
        results["checks"]["claims_preserved"] = claims_result["preserved"]
        results["checks"]["claims_missing"] = claims_result["missing"]
        
        if not claims_result["all_preserved"]:
            results["passed"] = False
            results["issues"].append(
                f"Missing claims: {', '.join(claims_result['missing'][:3])}"
            )
        
        # 3. Length ratio check
        length_ratio = len(rewritten.split()) / max(len(original.split()), 1)
        results["checks"]["length_ratio"] = round(length_ratio, 2)
        
        if length_ratio < self.LENGTH_RATIO_MIN:
            results["passed"] = False
            results["issues"].append(
                f"Too much content removed (ratio={length_ratio:.2f})"
            )
        elif length_ratio > self.LENGTH_RATIO_MAX:
            results["issues"].append(
                f"Too much content added (ratio={length_ratio:.2f})"
            )
            # This is a warning, not a failure
        
        # Determine action
        if not results["passed"]:
            if similarity < 0.6:
                results["action"] = "reject_retry_conservative"
            else:
                results["action"] = "retry_with_higher_fidelity"
        
        return results
    
    def _check_semantic_similarity(self, original: str, rewritten: str,
                                    original_embeddings: Dict[str, Any]) -> float:
        """Calculate semantic similarity between original and rewritten."""
        
        # Try to use embeddings if available
        if original_embeddings.get("available") and original_embeddings.get("full_text_embedding") is not None:
            try:
                from crew.stages.unpack import UnpackStage
                unpack = UnpackStage()
                return unpack.compute_text_similarity(original, rewritten)
            except Exception as e:
                pass
        
        # Fallback: word overlap (Jaccard similarity)
        words_orig = set(original.lower().split())
        words_new = set(rewritten.lower().split())
        
        if not words_orig or not words_new:
            return 0.0
        
        intersection = len(words_orig & words_new)
        union = len(words_orig | words_new)
        
        return intersection / union if union > 0 else 0.0
    
    def _verify_claims(self, text: str, claims: List[str]) -> Dict:
        """
        Verify that all claims appear in the rewritten text.
        
        Claims include dates, percentages, metrics, and proper nouns.
        """
        text_lower = text.lower()
        preserved = []
        missing = []
        
        for claim in claims:
            claim_str = str(claim).lower().strip()
            
            # Normalize the claim for matching
            # Handle date variations (Jun 2025 vs June 2025)
            claim_normalized = claim_str
            
            # Check if claim appears in text
            if claim_normalized in text_lower:
                preserved.append(claim)
            else:
                # Try flexible matching for numbers
                # "95%" might appear as "95 %" or "95 percent"
                if any(c.isdigit() for c in claim_str):
                    # Extract just the number
                    numbers = re.findall(r'\d+', claim_str)
                    if all(num in text for num in numbers):
                        preserved.append(claim)
                        continue
                
                missing.append(claim)
        
        return {
            "all_preserved": len(missing) == 0,
            "preserved": preserved,
            "missing": missing,
            "preservation_rate": len(preserved) / max(len(claims), 1)
        }
    
    def quick_check(self, original: str, rewritten: str) -> bool:
        """
        Quick sanity check without embeddings.
        Used for fast iterations.
        """
        # Basic checks
        orig_words = len(original.split())
        new_words = len(rewritten.split())
        
        if new_words < orig_words * 0.5:
            return False  # Lost too much
        
        if new_words > orig_words * 2:
            return False  # Added too much
        
        # Check key terms preserved
        # Extract numbers and percentages from original
        numbers = set(re.findall(r'\d+%?', original))
        numbers_in_new = set(re.findall(r'\d+%?', rewritten))
        
        # At least 80% of numbers should be preserved
        if numbers:
            preserved_ratio = len(numbers & numbers_in_new) / len(numbers)
            if preserved_ratio < 0.8:
                return False
        
        return True
