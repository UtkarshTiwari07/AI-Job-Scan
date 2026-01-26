# Stage A: Add Human Variability
# Rule-based injection of human-like patterns (deterministic, no hallucination)

"""
ADD VARIABILITY STAGE - Fourth step in HUMANIZE pipeline
Purpose: Inject human variability patterns using RULES (not LLM)

REVERSE-ENGINEERED from 4% AI detection text:
1. Quote metrics/percentages
2. Simplify formal vocabulary aggressively  
3. Add controlled imperfections
4. Remove "whilst", "sophisticated", "infrastructure"
5. Mix formality levels within same document
"""

import re
import random
from typing import List, Tuple


class AddVariabilityStage:
    """
    Inject human variability patterns using deterministic rules.
    
    Based on reverse-engineering what works to achieve 4% AI detection.
    """
    
    # Contraction mappings (human writing uses these naturally)
    CONTRACTIONS = {
        "I have": "I've",
        "I am": "I'm",
        "I will": "I'll",
        "I would": "I'd",
        "We have": "We've",
        "We are": "We're",
        "They have": "They've",
        "They are": "They're",
        "did not": "didn't",
        "do not": "don't",
        "does not": "doesn't",
        "was not": "wasn't",
        "were not": "weren't",
        "could not": "couldn't",
        "would not": "wouldn't",
        "should not": "shouldn't",
        "it is": "it's",
        "that is": "that's",
        "there is": "there's",
        "here is": "here's",
        "what is": "what's",
        "who is": "who's",
        "cannot": "can't",
        "will not": "won't",
    }
    
    # Action verb alternatives for breaking parallel structure
    VERB_ALTERNATIVES = {
        "Built": ["Created", "Developed", "Designed", "Constructed", "Set up"],
        "Developed": ["Built", "Created", "Designed", "Put together", "Made"],
        "Created": ["Built", "Developed", "Designed", "Put together", "Made"],
        "Led": ["Headed", "Directed", "Managed", "Oversaw", "Ran"],
        "Managed": ["Oversaw", "Directed", "Handled", "Ran", "Took charge of"],
        "Implemented": ["Built", "Deployed", "Created", "Put in place", "Set up"],
        "Designed": ["Created", "Put together", "Planned", "Developed", "Drew up"],
        "Improved": ["Made better", "Boosted", "Upgraded", "Strengthened", "Enhanced"],
        "Reduced": ["Cut", "Decreased", "Lowered", "Brought down", "Dropped"],
        "Increased": ["Grew", "Boosted", "Raised", "Bumped up", "Expanded"],
        "Achieved": ["Got", "Reached", "Hit", "Accomplished", "Pulled off"],
        "Delivered": ["Shipped", "Completed", "Got out", "Finished", "Released"],
        "Optimized": ["Improved", "Made faster", "Tuned", "Sped up", "Made better"],
        "Integrated": ["Connected", "Combined", "Linked", "Brought together", "Merged"],
        "Engineered": ["Built", "Created", "Designed", "Put together", "Made"],
        "Architected": ["Designed", "Created", "Built", "Planned", "Set up"],
        "Orchestrated": ["Coordinated", "Managed", "Ran", "Organized", "Handled"],
        "Drove": ["Led", "Headed", "Ran", "Pushed", "Managed"],
    }
    
    # AGGRESSIVE AI word replacement (reverse-engineered from 4% text)
    AI_BANNED_WORDS = {
        # Core AI fingerprints
        "leverage": "use",
        "utilize": "use",
        "spearhead": "lead",
        "orchestrate": "coordinate",
        "facilitate": "help",
        "streamline": "simplify",
        "comprehensive": "complete",
        "robust": "strong",
        "scalable": "flexible",
        "synergy": "teamwork",
        "paradigm": "approach",
        "holistic": "complete",
        "cutting-edge": "modern",
        "state-of-the-art": "latest",
        "passionate": "dedicated",
        "exceptional": "excellent",
        "unwavering": "consistent",
        "endeavor": "effort",
        "multifaceted": "varied",
        "meticulous": "careful",
        # NEW: From 4% analysis
        "whilst": "while",
        "sophisticated": "",  # Just remove it
        "enterprise-grade": "enterprise",
        "production-grade": "production",
        "architecting": "designing",
        "infrastructure": "setup",
        "demonstrating": "showing",
        "showcasing": "showing",
        "expertise": "experience",
        "proficiency": "skill",
        "adept": "skilled",
        "well-versed": "experienced",
        "seasoned": "experienced",
        "pivotal": "key",
        "paramount": "important",
        "subsequently": "then",
        "furthermore": "also",
        "moreover": "also",
        "additionally": "also",
        "consequently": "so",
        "henceforth": "from now on",
        "thereby": "so",
        "thus": "so",
        "hence": "so",
        "innovative": "",  # Remove
        "innovated": "created",
        "novel": "new",
        "seamless": "smooth",
        "seamlessly": "smoothly",
    }
    
    def inject_variability(self, text: str, register: str = "professional") -> Tuple[str, dict]:
        """
        Apply all variability transformations.
        """
        stats = {
            "contractions_added": 0,
            "parallel_structures_broken": 0,
            "ai_words_replaced": 0,
            "metrics_quoted": 0,
            "burstiness_cv": 0.0
        }
        
        result = text
        
        # 1. Replace AI banned words first (AGGRESSIVE)
        result, ai_count = self._replace_ai_words(result)
        stats["ai_words_replaced"] = ai_count
        
        # 2. Quote metrics and percentages (KEY PATTERN from 4% text)
        result, quote_count = self._quote_metrics(result)
        stats["metrics_quoted"] = quote_count
        
        # 3. Apply contractions (70% probability per occurrence)
        result, contraction_count = self._apply_contractions(result)
        stats["contractions_added"] = contraction_count
        
        # 4. Break parallel structure in bullet points
        result, parallel_count = self._break_parallel_structure(result)
        stats["parallel_structures_broken"] = parallel_count
        
        # 5. Add controlled imperfections
        result = self._add_controlled_imperfections(result)
        
        # 6. Calculate final burstiness
        stats["burstiness_cv"] = self._calculate_burstiness(result)
        
        return result, stats
    
    def _quote_metrics(self, text: str) -> Tuple[str, int]:
        """
        Wrap percentages and metrics in quotes.
        This is a KEY pattern from the 4% AI text.
        
        Example: "Achieved 90-95% improvement" -> 'Achieved "90-95%" improvement'
        """
        count = 0
        result = text
        
        # Pattern 1: Percentages like 90%, 90-95%, 45%
        # Only quote if not already quoted
        def quote_percentage(match):
            nonlocal count
            full = match.group(0)
            # Check if already quoted
            pre_char = match.string[max(0, match.start()-1):match.start()]
            post_char = match.string[match.end():match.end()+1] if match.end() < len(match.string) else ""
            
            if pre_char == '"' or post_char == '"':
                return full  # Already quoted
            
            count += 1
            return f'"{full}"'
        
        # Match percentages with optional ranges
        result = re.sub(r'\b(\d+(?:-\d+)?%)', quote_percentage, result)
        
        # Pattern 2: Quote significant numeric achievements
        # "500+ concurrent calls" -> '"500+ concurrent calls"'
        # But only do this sparingly (30% of the time)
        
        return result, count
    
    def _replace_ai_words(self, text: str) -> Tuple[str, int]:
        """Replace AI fingerprint words with human alternatives."""
        count = 0
        result = text
        
        for ai_word, human_word in self.AI_BANNED_WORDS.items():
            # Case-insensitive replacement
            pattern = re.compile(r'\b' + re.escape(ai_word) + r'\b', re.IGNORECASE)
            matches = pattern.findall(result)
            count += len(matches)
            
            # Replace preserving case (or remove if human_word is empty)
            def replace_match(match):
                original = match.group(0)
                if not human_word:  # Empty = remove word
                    return ""
                if original.isupper():
                    return human_word.upper()
                elif original[0].isupper():
                    return human_word.capitalize()
                return human_word
            
            result = pattern.sub(replace_match, result)
        
        # Clean up double spaces from removals
        result = re.sub(r'  +', ' ', result)
        result = re.sub(r' ,', ',', result)
        result = re.sub(r' \.', '.', result)
        
        return result, count
    
    def _apply_contractions(self, text: str, probability: float = 0.7) -> Tuple[str, int]:
        """Apply contractions with given probability."""
        count = 0
        result = text
        
        for long_form, contraction in self.CONTRACTIONS.items():
            if long_form.lower() in result.lower():
                # Apply with probability
                if random.random() < probability:
                    # Case-insensitive replacement
                    pattern = re.compile(re.escape(long_form), re.IGNORECASE)
                    matches = pattern.findall(result)
                    count += len(matches)
                    result = pattern.sub(contraction, result)
        
        return result, count
    
    def _break_parallel_structure(self, text: str) -> Tuple[str, int]:
        """
        If 2+ bullets start with the same verb, vary them.
        More aggressive than before - trigger on 2 not 3.
        """
        lines = text.split('\n')
        bullet_indices = {}  # word -> list of line indices
        
        # Find bullet points and their starting verbs
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('•') or stripped.startswith('-') or stripped.startswith('*'):
                # Extract first word after bullet
                words = stripped.lstrip('•-* ').split()
                if words:
                    first_word = words[0]
                    if first_word not in bullet_indices:
                        bullet_indices[first_word] = []
                    bullet_indices[first_word].append(i)
        
        # Replace verbs that appear 2+ times (more aggressive)
        changes_made = 0
        for word, indices in bullet_indices.items():
            if len(indices) >= 2 and word in self.VERB_ALTERNATIVES:
                alternatives = list(self.VERB_ALTERNATIVES[word])
                # Keep the first occurrence, change the rest
                for idx in indices[1:]:
                    if alternatives:
                        new_verb = random.choice(alternatives)
                        alternatives = [a for a in alternatives if a != new_verb]
                        
                        # Replace in the line
                        line = lines[idx]
                        for bullet in ['•', '-', '*']:
                            if bullet in line:
                                parts = line.split(bullet, 1)
                                if len(parts) == 2:
                                    rest = parts[1].strip()
                                    if rest.startswith(word):
                                        rest = new_verb + rest[len(word):]
                                        lines[idx] = parts[0] + bullet + ' ' + rest
                                        changes_made += 1
                                        break
        
        return '\n'.join(lines), changes_made
    
    def _add_controlled_imperfections(self, text: str) -> str:
        """
        Add subtle imperfections that humans naturally make.
        Reverse-engineered from the 4% AI text.
        """
        result = text
        
        # 1. Occasionally vary "production environment" phrasing
        if random.random() < 0.5:
            result = result.replace("production environments", "live environments")
            result = result.replace("production environment", "live environment")
        
        # 2. Replace some formal phrases with casual equivalents
        casual_swaps = [
            ("in order to", "to"),
            ("as well as", "and"),
            ("in addition to", "plus"),
            ("with regard to", "about"),
            ("in the context of", "in"),
            ("a large number of", "many"),
            ("a significant amount of", "a lot of"),
            ("on a daily basis", "daily"),
            ("at this point in time", "now"),
            ("in the event that", "if"),
        ]
        
        for formal, casual in casual_swaps:
            if formal in result.lower() and random.random() < 0.7:
                result = re.sub(re.escape(formal), casual, result, flags=re.IGNORECASE)
        
        # 3. Occasionally simplify verb phrases
        simple_verbs = [
            ("was responsible for", "handled"),
            ("played a key role in", "helped with"),
            ("was instrumental in", "helped"),
            ("successfully completed", "finished"),
            ("effectively managed", "managed"),
        ]
        
        for complex_verb, simple in simple_verbs:
            if complex_verb in result.lower():
                result = re.sub(re.escape(complex_verb), simple, result, flags=re.IGNORECASE)
        
        return result
    
    def _calculate_burstiness(self, text: str) -> float:
        """
        Calculate coefficient of variation (CV) for sentence lengths.
        Higher CV = more "bursty" = more human-like.
        Target: CV > 0.35
        """
        sentences = re.split(r'(?<=[.!?])\s+', text)
        lengths = [len(s.split()) for s in sentences if len(s.split()) > 0]
        
        if len(lengths) < 3:
            return 0.5  # Default for short texts
        
        avg = sum(lengths) / len(lengths)
        if avg == 0:
            return 0.0
        
        variance = sum((l - avg) ** 2 for l in lengths) / len(lengths)
        std = variance ** 0.5
        cv = std / avg
        
        return round(cv, 3)
    
    def needs_more_variation(self, text: str) -> Tuple[bool, str]:
        """Check if text needs more variation."""
        cv = self._calculate_burstiness(text)
        
        if cv < 0.25:
            return True, f"Burstiness too low (CV={cv:.2f})"
        
        # Check for parallel structure
        lines = text.split('\n')
        bullet_starts = {}
        for line in lines:
            stripped = line.strip()
            if any(stripped.startswith(b) for b in ['•', '-', '*']):
                words = stripped.lstrip('•-* ').split()
                if words:
                    first = words[0]
                    bullet_starts[first] = bullet_starts.get(first, 0) + 1
        
        max_repeat = max(bullet_starts.values()) if bullet_starts else 0
        if max_repeat >= 3:
            return True, f"Parallel structure ({max_repeat} bullets start same way)"
        
        return False, "OK"

