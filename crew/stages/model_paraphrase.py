# Stage M: Model Paraphrase + Style
# LLM-based controlled paraphrasing that preserves meaning

"""
MODEL PARAPHRASE STAGE - Third step in HUMANIZE pipeline
Purpose: Controlled rewriting that preserves meaning but changes AI patterns
"""

from typing import Optional


class ModelParaphraseStage:
    """
    LLM-based paraphrasing with style control.
    Uses high temperature for variety while preserving facts.
    """
    
    # Style-specific prompts - REVERSE ENGINEERED from 4% AI detection
    STYLE_PROMPTS = {
        "resume_professional": """You are rewriting resume text to DEFEAT AI detectors. Follow these rules EXACTLY:

NEVER CHANGE:
- Company names, job titles, dates, locations
- All percentages, metrics, and numbers (keep exact values)
- Technical terms and tool names

MANDATORY CHANGES (critical for low AI score):
1. REMOVE these words completely: sophisticated, innovative, cutting-edge, comprehensive, robust
2. REPLACE: "whilst" → "while", "utilize" → "use", "leverage" → "use", "infrastructure" → "setup"
3. REPLACE: "enterprise-grade" → "enterprise", "production-grade" → "production"
4. REPLACE: "architecting" → "designing", "orchestrating" → "coordinating"
5. SIMPLIFY formal transitions: "furthermore" → "also", "consequently" → "so", "subsequently" → "then"

SENTENCE STRUCTURE RULES:
6. Mix SHORT sentences (5-8 words) with LONG sentences (20+ words) - variation is key
7. Start consecutive bullets with DIFFERENT verbs - never 3 in a row same verb
8. Use casual phrases sometimes: "helped with", "set up", "put together", "ran"
9. Add brief connecting phrases: "basically", "pretty much", "kind of"

OUTPUT: Only the rewritten text. No explanations. No markdown formatting.""",

        "resume_concise": """Rewrite to be SHORT and PUNCHY while avoiding AI detection:

1. Cut unnecessary words ruthlessly
2. Keep all facts, dates, metrics EXACTLY as-is
3. Mix sentence lengths (some 5 words, some 20 words)
4. Use simple verbs: built, made, ran, led, helped
5. Remove: sophisticated, comprehensive, robust, innovative, leverage, utilize
6. Replace: whilst→while, infrastructure→setup, architecting→designing

OUTPUT: Only the rewritten text.""",

        "resume_detailed": """Expand with concrete details while avoiding AI patterns:

1. Keep all existing facts unchanged
2. Add specific context where helpful
3. Use conversational language sometimes
4. Vary sentence lengths dramatically
5. Remove AI words: sophisticated, comprehensive, robust, leverage, utilize
6. Use casual verbs: set up, put together, helped with, ran

OUTPUT: Only the rewritten text.""",

        "general": """Rewrite to sound like a real human wrote it:

1. Preserve all factual information exactly
2. MIX sentence lengths (short, then long, then medium)
3. Use simple words: use not utilize, help not facilitate
4. Remove: sophisticated, comprehensive, robust, innovative, cutting-edge
5. Add occasional casual phrases
6. Vary how sentences start

OUTPUT: Only the rewritten text."""
    }
    
    def __init__(self):
        self.default_style = "resume_professional"
    
    def paraphrase(self, text: str, style: str, llm, 
                   temperature: float = 0.85, 
                   claims: list = None) -> str:
        """
        Paraphrase text using LLM with style control.
        
        Args:
            text: Text to paraphrase
            style: Style key from STYLE_PROMPTS
            llm: LLM instance to use
            temperature: Generation temperature (higher = more variety)
            claims: List of claims that must be preserved
            
        Returns:
            Paraphrased text
        """
        # Get style prompt
        style_prompt = self.STYLE_PROMPTS.get(style, self.STYLE_PROMPTS["general"])
        
        # Add claims preservation reminder if claims provided
        claims_reminder = ""
        if claims:
            claims_list = ", ".join(str(c) for c in claims[:10])
            claims_reminder = f"\n\nCRITICAL - These MUST appear unchanged in output: {claims_list}"
        
        # Build full prompt
        prompt = f"""{style_prompt}{claims_reminder}

TEXT TO REWRITE:
{text}

REWRITTEN TEXT:"""
        
        # Generate using LLM
        try:
            # Use CrewAI's LLM call method
            from crewai import Task, Agent
            
            # Create a simple agent for this task
            rewriter_agent = Agent(
                role="Resume Rewriter",
                goal="Rewrite text naturally while preserving facts",
                backstory="Expert at making text sound human",
                llm=llm,
                verbose=False,
                allow_delegation=False
            )
            
            rewrite_task = Task(
                description=prompt,
                agent=rewriter_agent,
                expected_output="Rewritten text only"
            )
            
            result = rewrite_task.execute_sync()
            return self._clean_output(result)
            
        except Exception as e:
            # Fallback: return original with basic substitutions
            print(f"Paraphrase warning: {e}")
            return self._basic_substitution(text)
    
    def paraphrase_simple(self, text: str, style: str, llm) -> str:
        """
        Simplified paraphrase using direct LLM call.
        For when CrewAI overhead is not needed.
        """
        style_prompt = self.STYLE_PROMPTS.get(style, self.STYLE_PROMPTS["general"])
        
        prompt = f"""{style_prompt}

TEXT:
{text}

REWRITTEN:"""
        
        try:
            # Direct LiteLLM call
            import litellm
            response = litellm.completion(
                model=llm.model if hasattr(llm, 'model') else "gemini/gemini-2.0-flash",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.85,
                top_p=0.92
            )
            return self._clean_output(response.choices[0].message.content)
        except Exception as e:
            print(f"Simple paraphrase error: {e}")
            return text
    
    def _clean_output(self, text: str) -> str:
        """Clean LLM output of any formatting artifacts."""
        if not text:
            return ""
        
        # Remove common prefixes
        prefixes_to_remove = [
            "REWRITTEN TEXT:", "REWRITTEN:", "Here is the rewritten text:",
            "Here's the rewritten version:", "OUTPUT:"
        ]
        
        result = text.strip()
        for prefix in prefixes_to_remove:
            if result.upper().startswith(prefix.upper()):
                result = result[len(prefix):].strip()
        
        return result
    
    def _basic_substitution(self, text: str) -> str:
        """Fallback: basic word substitutions."""
        replacements = {
            "utilize": "use",
            "leverage": "use", 
            "spearhead": "lead",
            "orchestrate": "manage",
            "facilitate": "help",
            "comprehensive": "complete",
            "robust": "strong"
        }
        
        result = text
        for ai_word, human_word in replacements.items():
            result = result.replace(ai_word, human_word)
            result = result.replace(ai_word.title(), human_word.title())
        
        return result
    
    def choose_style(self, domain: str, register: str) -> str:
        """Choose appropriate style based on domain and register."""
        if domain == "resume":
            if register == "technical":
                return "resume_professional"
            elif register == "formal":
                return "resume_professional"
            else:
                return "resume_concise"
        return "general"
