# HUMANIZE Pipeline - Main Orchestrator
# 8-stage production humanization for 90%+ human accuracy

"""
HUMANIZE PIPELINE
=================

A production-level text humanization system based on linguistic research.

Stages:
H - Harmonize / Analyze (detect domain, tone, register, claims)
U - Unpack Semantics (capture embeddings for verification)
M - Model Paraphrase (LLM-based controlled rewriting)
A - Add Human Variability (rule-based injection)
N - Narrow & Check (semantic similarity, claim preservation)
I - Improve Fluency (constrained grammar post-edit)
Z - Zoom-out Classify (human-likeness scoring)
E - Evaluate & Monitor (metrics tracking)

Target: 90%+ human accuracy on external AI detectors
"""

import time
from typing import Dict, Any, Optional

from crew.stages import (
    HarmonizeStage,
    UnpackStage,
    ModelParaphraseStage,
    AddVariabilityStage,
    NarrowCheckStage,
    ImproveFluencyStage,
    ZoomClassifyStage,
    EvaluateStage
)


class HumanizePipeline:
    """
    HUMANIZE Pipeline - Multi-stage text humanization.
    
    Each stage addresses a specific risk:
    - Paraphrase keeps semantics
    - Variability makes text human
    - Post-edit prevents grammar errors
    - Classifier keeps you calibrated
    """
    
    def __init__(self, llm, verbose: bool = True):
        """
        Initialize the HUMANIZE pipeline.
        
        Args:
            llm: LLM instance for paraphrasing and grammar
            verbose: Whether to print progress
        """
        self.llm = llm
        self.verbose = verbose
        
        # Initialize all stages
        self.H = HarmonizeStage()     # Harmonize
        self.U = UnpackStage()         # Unpack
        self.M = ModelParaphraseStage() # Model
        self.A = AddVariabilityStage()  # Add variability
        self.N = NarrowCheckStage()     # Narrow/check
        self.I = ImproveFluencyStage()  # Improve
        self.Z = ZoomClassifyStage()    # Zoom classify
        self.E = EvaluateStage()        # Evaluate
    
    def humanize(self, text: str, max_retries: int = 3) -> Dict[str, Any]:
        """
        Run the full HUMANIZE pipeline.
        
        Args:
            text: Text to humanize
            max_retries: Maximum retry attempts if checks fail
            
        Returns:
            Dictionary with humanized text and all metrics
        """
        start_time = time.time()
        
        if self.verbose:
            print("\n" + "="*60)
            print("🧠 HUMANIZE Pipeline - Starting")
            print("="*60)
        
        # ═══════════════════════════════════════════════════════════
        # STAGE H: HARMONIZE / ANALYZE
        # ═══════════════════════════════════════════════════════════
        if self.verbose:
            print("\n[H] Harmonize: Analyzing text...")
        
        analysis = self.H.analyze(text)
        
        if self.verbose:
            print(f"    Domain: {analysis['domain']}")
            print(f"    Register: {analysis['register']}")
            print(f"    Claims: {len(analysis['claims'])} found")
        
        # ═══════════════════════════════════════════════════════════
        # STAGE U: UNPACK SEMANTICS
        # ═══════════════════════════════════════════════════════════
        if self.verbose:
            print("\n[U] Unpack: Capturing semantic embeddings...")
        
        embeddings = self.U.get_embeddings(text, analysis['claims'])
        
        if self.verbose:
            status = "✓ Ready" if embeddings.get('available') else "⚠ Fallback mode"
            print(f"    Embeddings: {status}")
        
        # ═══════════════════════════════════════════════════════════
        # STAGES M, A, N: RETRY LOOP
        # ═══════════════════════════════════════════════════════════
        best_result = None
        best_score = 0
        
        for attempt in range(max_retries):
            if self.verbose:
                print(f"\n[Attempt {attempt + 1}/{max_retries}]")
            
            # ─────────────────────────────────────────────────────────
            # STAGE M: MODEL PARAPHRASE
            # ─────────────────────────────────────────────────────────
            if self.verbose:
                print("\n[M] Model: Paraphrasing with style control...")
            
            style = self.M.choose_style(analysis['domain'], analysis['register'])
            
            try:
                paraphrased = self.M.paraphrase_simple(
                    text, style, self.llm
                )
            except Exception as e:
                if self.verbose:
                    print(f"    ⚠ Paraphrase error: {e}")
                paraphrased = text  # Fallback to original
            
            if self.verbose:
                print(f"    Style: {style}")
                print(f"    Length: {len(paraphrased.split())} words")
            
            # ─────────────────────────────────────────────────────────
            # STAGE A: ADD VARIABILITY
            # ─────────────────────────────────────────────────────────
            if self.verbose:
                print("\n[A] Add Variability: Injecting human patterns...")
            
            varied, var_stats = self.A.inject_variability(
                paraphrased, analysis['register']
            )
            
            if self.verbose:
                print(f"    AI words replaced: {var_stats['ai_words_replaced']}")
                print(f"    Contractions added: {var_stats['contractions_added']}")
                print(f"    Parallel structures broken: {var_stats['parallel_structures_broken']}")
                print(f"    Burstiness CV: {var_stats['burstiness_cv']:.3f}")
            
            # ─────────────────────────────────────────────────────────
            # STAGE N: NARROW & CHECK
            # ─────────────────────────────────────────────────────────
            if self.verbose:
                print("\n[N] Narrow: Checking semantic fidelity...")
            
            check = self.N.check(text, varied, embeddings, analysis['claims'])
            
            if self.verbose:
                print(f"    Semantic similarity: {check['checks'].get('semantic_similarity', 0):.3f}")
                print(f"    Claims preserved: {len(check['checks'].get('claims_preserved', []))}/{len(analysis['claims'])}")
                print(f"    Passed: {'✓' if check['passed'] else '✗'}")
            
            # Store best result so far
            preliminary_score = self.Z.score(varied)
            if preliminary_score['human_score'] > best_score:
                best_score = preliminary_score['human_score']
                best_result = {
                    'varied': varied,
                    'check': check,
                    'var_stats': var_stats
                }
            
            if check['passed']:
                break
            elif self.verbose:
                print(f"    Issues: {', '.join(check['issues'][:2])}")
                print("    Retrying with adjusted parameters...")
        
        # Use best result from attempts
        if best_result:
            varied = best_result['varied']
            check = best_result['check']
            var_stats = best_result['var_stats']
        
        # ═══════════════════════════════════════════════════════════
        # STAGE I: IMPROVE FLUENCY
        # ═══════════════════════════════════════════════════════════
        if self.verbose:
            print("\n[I] Improve: Applying grammar post-edit...")
        
        # Use rule-based for speed (LLM optional for thoroughness)
        fluent = self.I.improve_rule_based(varied)
        
        if self.verbose:
            print("    Applied: Rule-based corrections")
        
        # ═══════════════════════════════════════════════════════════
        # STAGE Z: ZOOM-OUT CLASSIFY
        # ═══════════════════════════════════════════════════════════
        if self.verbose:
            print("\n[Z] Zoom: Scoring human-likeness...")
        
        classification = self.Z.score(fluent)
        
        if self.verbose:
            print(f"    Human Score: {classification['human_score']}/100")
            print(f"    Passed: {'✓' if classification['passed'] else '✗'}")
            if classification['issues']:
                print(f"    Issues: {', '.join(classification['issues'][:3])}")
        
        # ═══════════════════════════════════════════════════════════
        # STAGE E: EVALUATE
        # ═══════════════════════════════════════════════════════════
        processing_time = time.time() - start_time
        
        similarity = check['checks'].get('semantic_similarity', 0.8)
        metrics = self.E.evaluate(
            text, fluent, classification, similarity, processing_time
        )
        
        if self.verbose:
            print("\n[E] Evaluate: Final metrics")
            print(f"    Human Score: {metrics['human_score']}/100")
            print(f"    Semantic Similarity: {metrics['semantic_similarity']:.3f}")
            print(f"    Processing Time: {metrics['processing_time_ms']:.0f}ms")
            print(f"    Passed All Checks: {'✓' if metrics['passed_all_checks'] else '✗'}")
            print("\n" + "="*60)
            print("🧠 HUMANIZE Pipeline - Complete")
            print("="*60 + "\n")
        
        return {
            "humanized_text": fluent,
            "human_score": classification['human_score'],
            "semantic_similarity": similarity,
            "issues": classification['issues'],
            "passed": classification['passed'] and check['passed'],
            "metrics": metrics,
            "analysis": analysis,
            "recommendation": classification.get('recommendation', '')
        }
    
    def quick_humanize(self, text: str) -> str:
        """
        Quick humanization without full pipeline.
        Just applies variability rules (Stage A only).
        
        Use for: Fast processing, when LLM calls are expensive
        """
        varied, _ = self.A.inject_variability(text, "professional")
        return varied
    
    def get_session_report(self) -> str:
        """Get a report of all humanizations in this session."""
        return self.E.generate_report()
