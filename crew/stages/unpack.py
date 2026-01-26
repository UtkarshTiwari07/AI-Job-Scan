# Stage U: Unpack Semantics
# Captures semantic embeddings to verify meaning preservation

"""
UNPACK STAGE - Second step in HUMANIZE pipeline
Purpose: Capture semantic representation of original text for later verification
"""

import re
from typing import Dict, List, Any
import numpy as np


class UnpackStage:
    """
    Capture semantic embeddings of the original text.
    Used later to verify that humanization preserved meaning.
    
    Uses sentence-transformers for lightweight local embedding.
    """
    
    def __init__(self):
        self.model = None  # Lazy load
    
    def _load_model(self):
        """Lazy load the sentence transformer model."""
        if self.model is None:
            try:
                from sentence_transformers import SentenceTransformer
                # all-MiniLM-L6-v2: fast, ~80MB, good quality
                self.model = SentenceTransformer('all-MiniLM-L6-v2')
            except ImportError:
                print("Warning: sentence-transformers not installed. Semantic checks disabled.")
                self.model = False
    
    def get_embeddings(self, text: str, claims: List[str]) -> Dict[str, Any]:
        """
        Get semantic embeddings for the full text and its components.
        
        Args:
            text: The full text to embed
            claims: List of factual claims to embed separately
            
        Returns:
            Dictionary with embeddings for full text, sentences, and claims
        """
        self._load_model()
        
        if self.model is False:
            # Fallback if sentence-transformers not available
            return {
                "full_text_embedding": None,
                "sentence_embeddings": [],
                "claim_embeddings": [],
                "available": False
            }
        
        sentences = self._split_sentences(text)
        
        return {
            "full_text_embedding": self.model.encode(text),
            "sentence_embeddings": [self.model.encode(s) for s in sentences[:20]],  # Limit to 20
            "claim_embeddings": [self.model.encode(c) for c in claims[:10]],  # Limit to 10
            "sentences": sentences,
            "available": True
        }
    
    def compute_similarity(self, emb1: np.ndarray, emb2: np.ndarray) -> float:
        """
        Compute cosine similarity between two embeddings.
        
        Args:
            emb1: First embedding
            emb2: Second embedding
            
        Returns:
            Cosine similarity score (0.0 to 1.0)
        """
        if emb1 is None or emb2 is None:
            return 0.5  # Default if embeddings not available
        
        try:
            from sentence_transformers import util
            return util.cos_sim(emb1, emb2).item()
        except:
            # Manual cosine similarity
            dot_product = np.dot(emb1, emb2)
            norm1 = np.linalg.norm(emb1)
            norm2 = np.linalg.norm(emb2)
            if norm1 == 0 or norm2 == 0:
                return 0.0
            return dot_product / (norm1 * norm2)
    
    def compute_text_similarity(self, text1: str, text2: str) -> float:
        """
        Compute similarity between two text strings.
        
        Args:
            text1: First text
            text2: Second text
            
        Returns:
            Cosine similarity score
        """
        self._load_model()
        
        if self.model is False:
            # Fallback: simple word overlap
            words1 = set(text1.lower().split())
            words2 = set(text2.lower().split())
            if not words1 or not words2:
                return 0.0
            intersection = len(words1 & words2)
            union = len(words1 | words2)
            return intersection / union if union > 0 else 0.0
        
        emb1 = self.model.encode(text1)
        emb2 = self.model.encode(text2)
        return self.compute_similarity(emb1, emb2)
    
    def _split_sentences(self, text: str) -> List[str]:
        """Split text into sentences."""
        # Split on sentence-ending punctuation
        sentences = re.split(r'(?<=[.!?])\s+', text)
        # Filter out empty and very short sentences
        return [s.strip() for s in sentences if len(s.strip()) > 10]
