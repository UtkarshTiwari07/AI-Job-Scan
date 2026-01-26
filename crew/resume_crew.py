"""
Resume Crew - Orchestrates the multi-agent resume generation pipeline
Rewritten with section-based iteration and quality gates
"""
import os
import re
import json
from dotenv import load_dotenv
from crewai import Agent, Task, Crew, Process, LLM

from agents.resume_generator import (
    create_resume_generator_agent,
    create_resume_generation_task
)
from agents.ai_detector import (
    create_ai_detector_agent,
    create_ai_detection_task
)
from agents.humanizer import (
    create_humanizer_agent,
    create_humanization_task
)
from agents.ats_scorer import (
    create_ats_scorer_agent,
    create_ats_scoring_task
)

# Load environment variables
load_dotenv()


def get_llm():
    """
    Initialize the LLM (Gemini API via CrewAI's LLM wrapper).
    Falls back to Groq if Gemini is not available.
    """
    gemini_key = os.getenv("GEMINI_API_KEY")
    groq_key = os.getenv("GROQ_API_KEY")
    
    if gemini_key and gemini_key != "your_gemini_api_key_here":
        return LLM(
            model="gemini/gemini-2.0-flash",
            api_key=gemini_key
        )
    elif groq_key and groq_key != "your_groq_api_key_here":
        return LLM(
            model="groq/llama-3.3-70b-versatile",
            api_key=groq_key
        )
    else:
        raise ValueError(
            "No API key found. Please set GEMINI_API_KEY or GROQ_API_KEY in your .env file. "
            "Get a free Gemini API key from https://aistudio.google.com/"
        )


def get_neuro_humanizer_llm():
    """
    Initialize the NeuroHumanizer LLM with aggressive settings.
    Based on research: high temperature + restricted sampling = unpredictable text.
    
    Settings:
    - temperature=0.9: Forces random word choices (high perplexity)
    - top_p=0.85: Nucleus sampling for variety
    """
    gemini_key = os.getenv("GEMINI_API_KEY")
    groq_key = os.getenv("GROQ_API_KEY")
    
    if gemini_key:
        return LLM(
            model="gemini/gemini-2.0-flash",
            api_key=gemini_key,
            temperature=0.9,
            top_p=0.85
        )
    elif groq_key:
        return LLM(
            model="groq/llama-3.3-70b-versatile",
            api_key=groq_key,
            temperature=0.9,
            top_p=0.85
        )
    return get_llm()


# ═══════════════════════════════════════════════════════════════
# AI FINGERPRINT WORDS - Words that appear in training data
# These must be replaced with human alternatives
# ═══════════════════════════════════════════════════════════════
AI_BANNED_WORDS = {
    # Overused AI verbs
    "spearheaded": "led",
    "leveraged": "used",
    "utilized": "used",
    "orchestrated": "managed",
    "streamlined": "simplified",
    "optimized": "improved",
    "architected": "designed",
    "pioneered": "started",
    "championed": "supported",
    "facilitated": "helped",
    
    # Corporate buzzwords
    "proven track record": "experience",
    "results-driven": "",
    "detail-oriented": "",
    "self-starter": "",
    "go-getter": "",
    "passionate about": "enjoy",
    "enthusiastic about": "like",
    "dedicated to": "focused on",
    "committed to": "focused on",
    
    # AI transitions
    "furthermore": "also",
    "moreover": "and",
    "additionally": "also",
    "consequently": "so",
    "subsequently": "then",
    "in addition": "also",
    "as a result": "so",
    
    # Vague qualifiers
    "various": "several",
    "numerous": "many",
    "significant": "big",
    "substantial": "large",
    "exceptional": "great",
    "outstanding": "excellent",
    "remarkable": "notable",
    
    # Abstract corporate speak
    "cross-functional": "across teams",
    "key stakeholders": "the team",
    "best practices": "good methods",
    "core competencies": "main skills",
    "strategic initiatives": "projects",
    "actionable insights": "useful findings",
    "cutting-edge": "modern",
    "state-of-the-art": "latest",
    "synergy": "teamwork",
    "paradigm": "approach",
    "holistic approach": "complete view",
    "robust solution": "solid solution",
    "seamlessly": "smoothly",
    "effectively": "",
    "efficiently": "",
    "successfully": "",
    
    # More AI fingerprints
    "delve": "explore",
    "tapestry": "mix",
    "landscape": "field",
    "unwavering": "steady",
    "testament": "proof",
    "multifaceted": "varied",
    "comprehensive": "full",
    "endeavor": "effort",
    "meticulous": "careful",
    "paramount": "important",
}


class ResumeCrew:
    """
    HUMANIZE-powered Resume Generation Pipeline.
    Uses 8-stage HUMANIZE pipeline for 90%+ humanization accuracy.
    """
    
    def __init__(self, max_iterations: int = 5):
        self.max_iterations = max_iterations
        self.llm = get_llm()
        self.neuro_llm = get_neuro_humanizer_llm()
        
        # Create agents
        self.resume_generator = create_resume_generator_agent(self.llm)
        self.ai_detector = create_ai_detector_agent(self.llm)
        self.humanizer = create_humanizer_agent(self.neuro_llm)
        self.ats_scorer = create_ats_scorer_agent(self.llm)
        
        # Initialize HUMANIZE pipeline for advanced humanization
        try:
            from crew.humanize_pipeline import HumanizePipeline
            self.humanize_pipeline = HumanizePipeline(self.neuro_llm, verbose=True)
            self.use_humanize_pipeline = True
        except ImportError as e:
            print(f"Warning: HUMANIZE pipeline not available: {e}")
            self.use_humanize_pipeline = False
    
    def generate_resume(
        self,
        original_resume: str,
        job_description: str,
        user_instructions: str = ""
    ) -> dict:
        """
        Run the complete resume generation pipeline with quality gates.
        
        Pipeline Flow:
        1. Generate ATS-optimized resume
        2. Score ATS match (if < 60%, regenerate once)
        3. Detect AI content in all sections
        4. While AI > 30% and iterations < max:
           - Humanize ONLY flagged sections
           - Merge humanized sections back
           - Re-detect ONLY humanized sections
        5. Final ATS score check
        
        Args:
            original_resume: Text content of the original resume
            job_description: Target job description
            user_instructions: Additional user preferences/instructions
            
        Returns:
            dict with final_resume, ai_score, ats_score, iterations, and status
        """
        print(f"\n{'='*60}")
        print(f"🚀 RESUME GENERATION PIPELINE STARTED")
        print(f"{'='*60}\n")
        
        # STEP 1: Generate initial ATS-optimized resume
        print("📝 STEP 1: Generating ATS-optimized resume...")
        generation_task = create_resume_generation_task(
            self.resume_generator,
            original_resume,
            job_description,
            user_instructions
        )
        
        generation_crew = Crew(
            agents=[self.resume_generator],
            tasks=[generation_task],
            process=Process.sequential,
            verbose=True
        )
        
        current_resume = str(generation_crew.kickoff())
        print("✅ Initial resume generated\n")
        
        # STEP 2: Calculate ATS score
        print("📊 STEP 2: Calculating ATS match score...")
        ats_score = self._calculate_ats_score(current_resume, job_description)
        print(f"ATS Score: {ats_score}%")
        
        # Quality Gate 1: ATS Score must be >= 60% to proceed
        if ats_score < 60:
            print("⚠️  ATS score too low, regenerating with keyword boost...")
            # Re-generate with emphasis on keywords
            current_resume = self._regenerate_with_keywords(
                original_resume, job_description, user_instructions
            )
            ats_score = self._calculate_ats_score(current_resume, job_description)
            print(f"New ATS Score: {ats_score}%\n")
        
        # STEP 3: AI Detection and Humanization Loop
        print("🔍 STEP 3: AI detection and humanization loop...")
        ai_score = 100
        iteration = 0
        
        while ai_score > 30 and iteration < self.max_iterations:
            iteration += 1
            print(f"\n--- Iteration {iteration}/{self.max_iterations} ---")
            
            # Detect AI content
            print("  🔍 Detecting AI content...")
            detection_result = self._detect_ai_content(current_resume)
            
            # Parse AI score
            try:
                detection_data = self._parse_detection_json(detection_result)
                ai_score = detection_data.get("overall_ai_score", 50)
            except json.JSONDecodeError:
                ai_score = self._parse_ai_score_fallback(detection_result)
            
            print(f"  AI Score: {ai_score}%")
            
            if ai_score <= 30:
                print("  ✅ AI score acceptable!")
                break
            
            # Humanize ENTIRE resume (more reliable than section-based)
            print("  ✍️  Humanizing full resume...")
            current_resume = self._humanize_full_resume(current_resume, detection_result)
            print("  ✅ Resume humanized")
        
        # STEP 3.5: Apply rule-based anti-AI post-processing
        print("\n🔧 STEP 3.5: Applying rule-based anti-AI post-processing...")
        current_resume = self._apply_anti_ai_rules(current_resume)
        print("  ✅ Anti-AI rules applied")
        
        # STEP 4: Final ATS score
        print(f"\n📊 STEP 4: Final ATS score calculation...")
        final_ats_score = self._calculate_ats_score(current_resume, job_description)
        print(f"Final ATS Score: {final_ats_score}%")
        print(f"Final AI Score: {ai_score}%")
        
        # Determine status
        status = "success"
        if final_ats_score < 80:
            status = "warning_ats_low"
        if ai_score > 30:
            status = "warning_ai_high"
        if final_ats_score < 80 and ai_score > 30:
            status = "failed_quality_gates"
        
        print(f"\n{'='*60}")
        print(f"✨ PIPELINE COMPLETE - Status: {status}")
        print(f"{'='*60}\n")
        
        return {
            "final_resume": current_resume.strip(),
            "ai_score": ai_score,
            "ats_score": final_ats_score,
            "iterations": iteration,
            "status": status
        }
    
    def _apply_anti_ai_rules(self, resume: str) -> str:
        """
        NeuroHumanizer Post-Processing: Apply deterministic anti-AI transformations.
        Uses the AI_BANNED_WORDS dictionary for vocabulary substitution.
        """
        result = resume
        
        # Step 1: Apply AI banned word substitutions (case-insensitive)
        for ai_word, human_word in AI_BANNED_WORDS.items():
            # Handle exact matches
            result = result.replace(ai_word, human_word)
            # Handle title case
            result = result.replace(ai_word.title(), human_word.title() if human_word else "")
            # Handle uppercase
            result = result.replace(ai_word.upper(), human_word.upper() if human_word else "")
        
        # Step 2: Force contractions (humans use them naturally)
        contraction_map = {
            "I have": "I've",
            "I am": "I'm", 
            "We have": "We've",
            "They have": "They've",
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
        
        for long_form, contraction in contraction_map.items():
            result = result.replace(long_form, contraction)
            result = result.replace(long_form.capitalize(), contraction.capitalize())
        
        # Step 3: Clean up artifacts
        result = re.sub(r'  +', ' ', result)  # Double spaces
        result = re.sub(r'\n{3,}', '\n\n', result)  # Multiple newlines
        result = re.sub(r'^\s+$', '', result, flags=re.MULTILINE)  # Blank lines with spaces
        
        return result.strip()
    
    def _calculate_ats_score(self, resume: str, job_description: str) -> int:
        """Calculate ATS match score using ATS Scoring Agent."""
        scoring_task = create_ats_scoring_task(
            self.ats_scorer,
            resume,
            job_description
        )
        
        scoring_crew = Crew(
            agents=[self.ats_scorer],
            tasks=[scoring_task],
            process=Process.sequential,
            verbose=False
        )
        
        result = str(scoring_crew.kickoff())
        
        # Parse ATS score from result
        patterns = [
            r"ATS SCORE:\s*(\d+)%",
            r"TOTAL.*?(\d+)%",
            r"Score:\s*(\d+)%"
        ]
        
        for pattern in patterns:
            match = re.search(pattern, result, re.IGNORECASE)
            if match:
                return int(match.group(1))
        
        return 50  # Default if parsing fails
    
    def _detect_ai_content(self, resume: str) -> str:
        """
        Detect AI content using RULE-BASED detection (not LLM).
        This avoids the circular problem of using the same LLM for detection.
        Returns a JSON-like string for compatibility with existing parsing.
        """
        score, issues = self._rule_based_ai_detection(resume)
        
        # Format as JSON for compatibility with existing code
        result = {
            "overall_ai_score": score,
            "flagged_sections": [{
                "section_name": "Full Resume",
                "content": resume[:500],  # First 500 chars
                "ai_probability": score,
                "issues": issues
            }] if score > 30 else [],
            "clean_sections": []
        }
        
        return json.dumps(result)
    
    def _rule_based_ai_detection(self, text: str) -> tuple:
        """
        COMPREHENSIVE AI Detection System
        Mimics real detectors like GPTZero, Originality.ai, ZeroGPT
        
        Detection Signals:
        1. AI phrase artifacts (training data leakage)
        2. Perplexity simulation (word predictability)
        3. Burstiness (sentence length variation)
        4. Stylometric uniformity (writing style consistency)
        5. N-gram patterns (common AI sequences)
        6. Structural patterns (bullet formatting)
        7. Vocabulary diversity (type-token ratio)
        8. Contraction usage
        9. First-person voice patterns
        10. Hedging language
        
        Returns (score: int, issues: list[str])
        """
        issues = []
        total_penalty = 0
        text_lower = text.lower()
        words = text_lower.split()
        word_count = len(words)
        
        if word_count < 50:
            return 20, ["Text too short for accurate detection"]
        
        # ═══════════════════════════════════════════════════════════════
        # SIGNAL 1: AI Training Artifacts (25% weight)
        # These phrases appear in AI training data and leak into outputs
        # ═══════════════════════════════════════════════════════════════
        ai_phrases = [
            # Overused AI verbs
            "spearheaded", "leveraged", "utilized", "orchestrated", "streamlined",
            "optimized", "architected", "pioneered", "championed", "facilitated",
            
            # Corporate buzzwords AI loves
            "proven track record", "results-driven", "detail-oriented", "self-starter",
            "go-getter", "team player", "think outside the box", "hit the ground running",
            "passionate about", "enthusiastic about", "dedicated to", "committed to",
            
            # AI's favorite transitions
            "furthermore", "moreover", "additionally", "consequently", "subsequently",
            "in addition", "as a result", "in conclusion", "to summarize",
            
            # Vague qualifiers
            "various", "numerous", "significant", "substantial", "considerable",
            "exceptional", "outstanding", "remarkable", "impressive",
            
            # Abstract corporate speak
            "cross-functional", "key stakeholders", "best practices", "core competencies",
            "strategic initiatives", "actionable insights", "cutting-edge", "state-of-the-art",
            "synergy", "paradigm", "holistic approach", "robust solution", "scalable",
            "seamlessly", "effectively", "efficiently", "successfully",
            
            # AI achievement patterns
            "exceeded expectations", "drove growth", "delivered results",
            "demonstrated ability", "proven ability", "strong background",
            "extensive experience", "in-depth knowledge", "hands-on experience"
        ]
        
        found_phrases = [p for p in ai_phrases if p in text_lower]
        phrase_penalty = min(25, len(found_phrases) * 3)  # Max 25 points
        total_penalty += phrase_penalty
        if found_phrases:
            issues.append(f"AI artifacts ({len(found_phrases)}): {', '.join(found_phrases[:4])}...")
        
        # ═══════════════════════════════════════════════════════════════
        # SIGNAL 2: Perplexity Simulation (20% weight)
        # AI text has low perplexity - every word is predictable
        # We simulate this by checking for common word pairs
        # ═══════════════════════════════════════════════════════════════
        predictable_pairs = [
            ("the", "following"), ("in", "order"), ("as", "well"), ("due", "to"),
            ("in", "addition"), ("with", "expertise"), ("a", "strong"),
            ("the", "ability"), ("and", "the"), ("of", "the"), ("to", "the"),
            ("in", "the"), ("for", "the"), ("with", "the"), ("on", "the"),
            ("demonstrated", "expertise"), ("proven", "ability"), ("strong", "background"),
            ("extensive", "experience"), ("in-depth", "knowledge"), ("hands-on", "experience"),
            ("highly", "motivated"), ("results", "oriented"), ("detail", "oriented"),
        ]
        
        pair_count = 0
        for i in range(len(words) - 1):
            if (words[i], words[i+1]) in predictable_pairs:
                pair_count += 1
        
        pair_density = (pair_count / (word_count / 100)) if word_count > 0 else 0
        if pair_density > 2:
            perplexity_penalty = min(20, int(pair_density * 3))
            total_penalty += perplexity_penalty
            issues.append(f"Low perplexity: {pair_count} predictable word pairs")
        
        # ═══════════════════════════════════════════════════════════════
        # SIGNAL 3: Burstiness Analysis (15% weight)
        # Human writing varies wildly in sentence length
        # AI tends to write sentences of similar length
        # ═══════════════════════════════════════════════════════════════
        sentences = re.split(r'[.!?]+', text)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 5]
        
        if len(sentences) >= 5:
            lengths = [len(s.split()) for s in sentences]
            avg_len = sum(lengths) / len(lengths)
            variance = sum((l - avg_len) ** 2 for l in lengths) / len(lengths)
            std_dev = variance ** 0.5
            
            # Coefficient of variation (CV) - normalized measure
            cv = (std_dev / avg_len) if avg_len > 0 else 0
            
            if cv < 0.3:  # Low variation = very AI-like
                burstiness_penalty = min(15, int((0.3 - cv) * 50))
                total_penalty += burstiness_penalty
                issues.append(f"Low burstiness (CV={cv:.2f}): sentences too uniform")
        
        # ═══════════════════════════════════════════════════════════════
        # SIGNAL 4: Vocabulary Diversity - Type-Token Ratio (10% weight)
        # AI tends to reuse the same words; humans use more variety
        # ═══════════════════════════════════════════════════════════════
        unique_words = set(words)
        ttr = len(unique_words) / word_count if word_count > 0 else 0
        
        if ttr < 0.4:  # Low diversity = AI-like
            vocab_penalty = min(10, int((0.4 - ttr) * 50))
            total_penalty += vocab_penalty
            issues.append(f"Low vocabulary diversity (TTR={ttr:.2f})")
        
        # ═══════════════════════════════════════════════════════════════
        # SIGNAL 5: Parallel Structure Detection (10% weight)
        # AI loves to repeat "Verb + Object + Metric" patterns
        # ═══════════════════════════════════════════════════════════════
        bullet_pattern = re.findall(r'[•\-\*]\s*([A-Z][a-z]+(?:ed|ing)?)', text)
        if len(bullet_pattern) >= 4:
            word_freq = {}
            for w in bullet_pattern:
                word_freq[w] = word_freq.get(w, 0) + 1
            max_repeat = max(word_freq.values())
            repeat_ratio = max_repeat / len(bullet_pattern)
            
            if repeat_ratio > 0.3:
                parallel_penalty = min(10, int(repeat_ratio * 20))
                total_penalty += parallel_penalty
                issues.append(f"Parallel structure: {max_repeat}/{len(bullet_pattern)} bullets start same")
        
        # ═══════════════════════════════════════════════════════════════
        # SIGNAL 6: Contraction Analysis (5% weight)
        # Humans naturally use contractions; AI often doesn't
        # ═══════════════════════════════════════════════════════════════
        contractions = ["'ve", "'re", "'ll", "'d", "n't", "'m"]
        contraction_count = sum(text.count(c) for c in contractions)
        contraction_ratio = contraction_count / (word_count / 100) if word_count > 0 else 0
        
        if contraction_ratio < 0.5:
            contraction_penalty = min(5, int((0.5 - contraction_ratio) * 10))
            total_penalty += contraction_penalty
            if contraction_count == 0:
                issues.append("No contractions (humans use contractions)")
        
        # ═══════════════════════════════════════════════════════════════
        # SIGNAL 7: First-Person Authenticity (5% weight)
        # AI tends toward robotic third-person or passive voice
        # ═══════════════════════════════════════════════════════════════
        first_person = text_lower.count(" i ") + text_lower.count("i've") + text_lower.count("i'm")
        passive_markers = sum(text_lower.count(m) for m in 
                             ["was responsible", "were responsible", "was involved", "were involved",
                              "was tasked", "been", "was given", "were given"])
        
        if passive_markers > first_person and passive_markers > 2:
            total_penalty += 5
            issues.append(f"Passive voice dominant ({passive_markers} passive vs {first_person} first-person)")
        
        # ═══════════════════════════════════════════════════════════════
        # SIGNAL 8: Hedging Language (5% weight)
        # AI tends to hedge with qualifiers; confident humans are direct
        # ═══════════════════════════════════════════════════════════════
        hedges = ["may", "might", "could", "possibly", "potentially", "likely",
                 "generally", "typically", "usually", "often", "sometimes"]
        hedge_count = sum(text_lower.count(f" {h} ") for h in hedges)
        
        if hedge_count > 3:
            total_penalty += min(5, hedge_count)
            issues.append(f"Hedging language: {hedge_count} qualifiers")
        
        # ═══════════════════════════════════════════════════════════════
        # SIGNAL 9: Sentence Starters Analysis (5% weight)
        # AI has limited variety in how it starts sentences
        # ═══════════════════════════════════════════════════════════════
        sentence_starters = [s.split()[0].lower() if s.split() else "" for s in sentences]
        if len(sentence_starters) >= 5:
            starter_variety = len(set(sentence_starters)) / len(sentence_starters)
            if starter_variety < 0.5:
                total_penalty += 5
                issues.append(f"Repetitive sentence starters (variety={starter_variety:.2f})")
        
        # ═══════════════════════════════════════════════════════════════
        # FINAL SCORE CALCULATION
        # ═══════════════════════════════════════════════════════════════
        # Base penalty for any generated content (LLMs have baseline patterns)
        base_penalty = 25  # Assume some AI characteristics in any generated text
        
        final_score = min(100, base_penalty + total_penalty)
        
        return final_score, issues
    
    def _parse_detection_json(self, detection_result: str) -> dict:
        """Parse JSON from detection result."""
        # Try to extract JSON from the result
        json_match = re.search(r'\{[\s\S]*\}', detection_result)
        if json_match:
            return json.loads(json_match.group(0))
        raise json.JSONDecodeError("No JSON found", detection_result, 0)
    
    def _parse_ai_score_fallback(self, detection_result: str) -> int:
        """Fallback AI score parser if JSON fails."""
        patterns = [
            r"AI.*?SCORE:\s*(\d+)%",
            r"overall.*?(\d+)%",
            r"probability.*?(\d+)%"
        ]
        
        for pattern in patterns:
            match = re.search(pattern, detection_result, re.IGNORECASE)
            if match:
                return int(match.group(1))
        
        return 50
    
    def _humanize_sections(self, flagged_sections: list, full_resume: str) -> list:
        """Humanize specific flagged sections."""
        humanization_task = create_humanization_task(
            self.humanizer,
            flagged_sections,
            full_resume
        )
        
        humanization_crew = Crew(
            agents=[self.humanizer],
            tasks=[humanization_task],
            process=Process.sequential,
            verbose=False
        )
        
        result = str(humanization_crew.kickoff())
        return self._parse_humanized_sections(result)
    
    def _parse_humanized_sections(self, humanization_result: str) -> list:
        """Parse humanized sections from agent output."""
        sections = []
        # Split by "SECTION:" markers
        parts = re.split(r'SECTION:\s*(.+?)\n', humanization_result)
        
        for i in range(1, len(parts), 2):
            if i + 1 < len(parts):
                section_name = parts[i].strip()
                content = parts[i + 1].strip()
                # Remove "REWRITTEN CONTENT:" prefix
                content = re.sub(r'REWRITTEN CONTENT:\s*', '', content, flags=re.IGNORECASE)
                sections.append({
                    "section_name": section_name,
                    "content": content
                })
        
        return sections
    
    def _merge_sections(self, resume: str, humanized_sections: list) -> str:
        """Merge humanized sections back into the full resume."""
        updated_resume = resume
        
        for section in humanized_sections:
            section_name = section.get("section_name", "")
            new_content = section.get("content", "")
            
            if not section_name or not new_content:
                continue
            
            # Clean up the new content - remove any "---" separators or extra markers
            new_content = re.sub(r'^---+\s*$', '', new_content, flags=re.MULTILINE).strip()
            new_content = re.sub(r'REWRITTEN CONTENT:\s*', '', new_content, flags=re.IGNORECASE).strip()
            
            # Strategy 1: Try to find section header and replace content
            # Handle various header formats: "EXPERIENCE", "**Experience**", "Experience:", "## Experience"
            base_name = section_name.split(' - ')[0].split(':')[0].strip()
            
            patterns = [
                # Markdown headers: ## Experience
                rf"(##\s*{re.escape(base_name)}.*?\n)(.*?)(?=\n##|\n\*\*[A-Z]|\Z)",
                # Bold markdown: **Experience**
                rf"(\*\*{re.escape(base_name)}\*\*:?\s*\n)(.*?)(?=\n\*\*[A-Z]|\n##|\Z)",
                # All caps headers: EXPERIENCE
                rf"({re.escape(base_name.upper())}:?\s*\n)(.*?)(?=\n[A-Z]{{2,}}[^a-z]|\n##|\n\*\*|\Z)",
                # Title case with colon: Experience:
                rf"({re.escape(base_name)}:?\s*\n)(.*?)(?=\n[A-Z][a-z]+:|\n##|\n\*\*|\Z)",
            ]
            
            merged = False
            for pattern in patterns:
                match = re.search(pattern, updated_resume, re.DOTALL | re.IGNORECASE)
                if match:
                    header = match.group(1)
                    updated_resume = re.sub(
                        pattern,
                        f"{header}{new_content}\n\n",
                        updated_resume,
                        count=1,
                        flags=re.DOTALL | re.IGNORECASE
                    )
                    merged = True
                    print(f"    ✓ Merged section: {section_name}")
                    break
            
            if not merged:
                # Fallback: If exact section not found but it's a job entry, try matching company name
                if " - " in section_name or " at " in section_name.lower():
                    # It's likely a job entry like "Experience - Software Engineer at Google"
                    # Just use the full humanized resume for this iteration
                    print(f"    ⚠️ Could not find section '{section_name}' for merging, using humanized content")
                else:
                    print(f"    ⚠️ Could not find section '{section_name}' for merging")
        
        return updated_resume
    
    def _humanize_full_resume(self, resume: str, detection_feedback: str) -> str:
        """
        Humanize entire resume using HUMANIZE pipeline.
        Falls back to CrewAI agent if pipeline not available.
        """
        # Try HUMANIZE pipeline first (8-stage production system)
        if hasattr(self, 'use_humanize_pipeline') and self.use_humanize_pipeline:
            try:
                print("\n🧠 Using HUMANIZE Pipeline (8-stage)")
                result = self.humanize_pipeline.humanize(resume)
                
                if result.get('passed', False):
                    print(f"✓ HUMANIZE complete - Score: {result['human_score']}/100")
                    return result['humanized_text']
                else:
                    print(f"⚠ HUMANIZE partial - Score: {result['human_score']}/100")
                    # Still use the result, but apply additional post-processing
                    return self._apply_anti_ai_rules(result['humanized_text'])
                    
            except Exception as e:
                print(f"⚠ HUMANIZE pipeline error: {e}, falling back to agent")
        
        # Fallback: Use CrewAI agent method
        fake_section = [{
            "section_name": "Full Resume",
            "content": resume,
            "ai_probability": 50,
            "issues": ["Full humanization pass"]
        }]
        
        humanization_task = create_humanization_task(
            self.humanizer,
            fake_section,
            resume
        )
        
        humanization_crew = Crew(
            agents=[self.humanizer],
            tasks=[humanization_task],
            process=Process.sequential,
            verbose=False
        )
        
        raw_result = str(humanization_crew.kickoff())
        
        # Clean any stray markers that might still appear
        cleaned = self._clean_resume_output(raw_result)
        return cleaned
    
    def _clean_resume_output(self, resume: str) -> str:
        """Remove any AI output markers that shouldn't be in final resume."""
        patterns_to_remove = [
            r'^SECTION:.*$',
            r'^REWRITTEN CONTENT:\s*$',
            r'^---+$',
            r'^AI Probability:.*$',
            r'^Issues:.*$',
            r'^\[.*exact text.*\]$',
            r'^Original Content:\s*$',
            r'^Humanized Content:\s*$',
            r'^Full Resume$',
            r'^Begin rewriting.*$',
        ]
        
        result = resume
        for pattern in patterns_to_remove:
            result = re.sub(pattern, '', result, flags=re.MULTILINE | re.IGNORECASE)
        
        # Collapse multiple newlines
        result = re.sub(r'\n{3,}', '\n\n', result)
        return result.strip()
    
    def _regenerate_with_keywords(
        self, 
        original_resume: str,
        job_description: str,
        user_instructions: str
    ) -> str:
        """Regenerate resume with extra emphasis on keywords."""
        enhanced_instructions = f"""{user_instructions}
        
CRITICAL: This resume MUST incorporate ALL keywords from the job description.
Use exact phrases. Aim for 80%+ keyword match."""
        
        generation_task = create_resume_generation_task(
            self.resume_generator,
            original_resume,
            job_description,
            enhanced_instructions
        )
        
        generation_crew = Crew(
            agents=[self.resume_generator],
            tasks=[generation_task],
            process=Process.sequential,
            verbose=False
        )
        
        return str(generation_crew.kickoff())


if __name__ == "__main__":
    # Quick test
    crew = ResumeCrew()
    print("Crew initialized successfully!")
