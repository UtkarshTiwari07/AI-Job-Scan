# Stage I: Improve Fluency
# Grammar post-edit that preserves humanness

"""
IMPROVE FLUENCY STAGE - Sixth step in HUMANIZE pipeline
Purpose: Fix grammar errors WITHOUT sterilizing the human-like style
"""


class ImproveFluencyStage:
    """
    Constrained grammar correction.
    
    Critical: We fix ONLY actual errors, not style.
    We must preserve:
    - Contractions (they're human!)
    - Casual phrasing
    - Sentence fragments (intentional)
    - Varied sentence lengths
    """
    
    GRAMMAR_PROMPT = """You are a careful grammar editor. Fix ONLY obvious errors.

CRITICAL RULES - READ CAREFULLY:
1. DO NOT change word choice or style
2. DO NOT remove contractions (I've, didn't, won't are CORRECT)
3. DO NOT make text more formal
4. DO NOT change sentence lengths or structure
5. DO NOT add or remove words except to fix errors

ONLY fix these types of errors:
- Subject-verb agreement errors
- Clear punctuation mistakes (missing periods, wrong apostrophes)
- Obvious typos/misspellings
- Missing articles (a, an, the) where grammatically required

If the text has no obvious errors, return it UNCHANGED.

TEXT TO CHECK:
{text}

CORRECTED TEXT (minimal changes only):"""

    def improve(self, text: str, llm) -> str:
        """
        Apply minimal grammar corrections.
        
        Args:
            text: Text to check
            llm: LLM instance for grammar checking
            
        Returns:
            Corrected text (or original if no errors)
        """
        try:
            import litellm
            
            response = litellm.completion(
                model=llm.model if hasattr(llm, 'model') else "gemini/gemini-2.0-flash",
                messages=[{"role": "user", "content": self.GRAMMAR_PROMPT.format(text=text)}],
                temperature=0.2,  # Low temperature for consistency
                max_tokens=4000
            )
            
            result = response.choices[0].message.content.strip()
            
            # Validate the result didn't change too much
            if self._changes_acceptable(text, result):
                return result
            else:
                # Too many changes - return original
                print("Grammar edit made too many changes, keeping original")
                return text
                
        except Exception as e:
            print(f"Grammar check error: {e}")
            return text
    
    def improve_rule_based(self, text: str) -> str:
        """
        Rule-based grammar fixes (no LLM).
        Faster and more predictable.
        """
        import re
        result = text
        
        # Fix double spaces
        result = re.sub(r'  +', ' ', result)
        
        # Fix space before punctuation
        result = re.sub(r'\s+([.,!?;:])', r'\1', result)
        
        # Fix missing space after punctuation
        result = re.sub(r'([.,!?;:])([A-Za-z])', r'\1 \2', result)
        
        # Fix multiple newlines
        result = re.sub(r'\n{3,}', '\n\n', result)
        
        # Fix common typos
        typo_fixes = {
            " teh ": " the ",
            " adn ": " and ",
            " taht ": " that ",
            " wiht ": " with ",
            " fo ": " of ",
        }
        for typo, fix in typo_fixes.items():
            result = result.replace(typo, fix)
        
        return result
    
    def _changes_acceptable(self, original: str, corrected: str) -> bool:
        """
        Validate that corrections are minimal.
        If too many words changed, reject the corrections.
        """
        orig_words = original.lower().split()
        corr_words = corrected.lower().split()
        
        # Calculate word-level difference
        orig_set = set(orig_words)
        corr_set = set(corr_words)
        
        # Symmetric difference - words in one but not both
        diff = orig_set.symmetric_difference(corr_set)
        
        # Allow max 5% word changes
        max_changes = max(len(orig_words) * 0.05, 5)  # At least 5 words
        
        return len(diff) <= max_changes
    
    def verify_contractions_preserved(self, original: str, result: str) -> bool:
        """Check that contractions weren't removed."""
        import re
        
        # Find contractions in original
        orig_contractions = set(re.findall(r"\b\w+'\w+\b", original.lower()))
        result_contractions = set(re.findall(r"\b\w+'\w+\b", result.lower()))
        
        # All original contractions should still be there
        return orig_contractions.issubset(result_contractions)
