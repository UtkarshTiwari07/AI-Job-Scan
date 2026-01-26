# Stage U: Unpack Semantics
# Captures semantic embeddings to verify meaning preservation

"""
UNPACK STAGE - Second step in HUMANIZE pipeline
Purpose: Capture semantic representation using Gemini Embeddings API (Serverless friendly)
"""

import re
import os
import numpy as np
import google.generativeai as genai
from typing import Dict, List, Any


class UnpackStage:
    """
    Capture semantic embeddings of the original text.
    Used later to verify that humanization preserved meaning.
    
    Uses Gemini Embeddings API for lightweight, serverless operation.
    """
    
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        if self.api_key:
            genai.configure(api_key=self.api_key)
            self.model_name = "models/text-embedding-004"
        else:
            self.model_name = None
    
    def get_embeddings(self, text: str, claims: List[str]) -> Dict[str, Any]:
        """
        Get semantic embeddings for the full text and its components.
        
        Args:
            text: The full text to embed
            claims: List of factual claims to embed separately
            
        Returns:
            Dictionary with embeddings for full text, sentences, and claims
        """
        if not self.model_name:
            # Fallback if API key missing
            return self._fallback_response(text)
        
        try:
            # Get full text embedding
            full_emb = genai.embed_content(
                model=self.model_name,
                content=text,
                task_type="retrieval_document"
            )['embedding']
            
            sentences = self._split_sentences(text)
            
            # Batch embed sentences (limit 20 to avoid rate limits)
            sent_embs = []
            if sentences:
                batch = sentences[:20]
                batch_result = genai.embed_content(
                    model=self.model_name,
                    content=batch,
                    task_type="retrieval_document"
                )
                sent_embs = batch_result['embedding']
            
            # Batch embed claims (limit 10)
            claim_embs = []
            if claims:
                batch = claims[:10]
                batch_result = genai.embed_content(
                    model=self.model_name,
                    content=batch,
                    task_type="retrieval_document"
                )
                claim_embs = batch_result['embedding']
            
            return {
                "full_text_embedding": np.array(full_emb),
                "sentence_embeddings": [np.array(e) for e in sent_embs],
                "claim_embeddings": [np.array(e) for e in claim_embs],
                "sentences": sentences,
                "available": True
            }
            
        except Exception as e:
            print(f"Embedding API error: {e}")
            return self._fallback_response(text)
    
    def _fallback_response(self, text: str):
        """Return empty structure if embedding fails."""
        return {
            "full_text_embedding": None,
            "sentence_embeddings": [],
            "claim_embeddings": [],
            "sentences": self._split_sentences(text),
            "available": False
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
            
        # Ensure numpy arrays
        v1 = np.array(emb1)
        v2 = np.array(emb2)
        
        # Compute cosine similarity manually (no scikit-learn dependency needed)
        dot_product = np.dot(v1, v2)
        norm1 = np.linalg.norm(v1)
        norm2 = np.linalg.norm(v2)
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
            
        return float(dot_product / (norm1 * norm2))
    
    def compute_text_similarity(self, text1: str, text2: str) -> float:
        """
        Compute similarity between two text strings using API.
        
        Args:
            text1: First text
            text2: Second text
            
        Returns:
            Cosine similarity score
        """
        if not self.model_name:
            return self._overlap_similarity(text1, text2)
            
        try:
            emb1 = genai.embed_content(
                model=self.model_name,
                content=text1,
                task_type="similarity"
            )['embedding']
            
            emb2 = genai.embed_content(
                model=self.model_name,
                content=text2,
                task_type="similarity"
            )['embedding']
            
            return self.compute_similarity(emb1, emb2)
            
        except Exception as e:
            print(f"Similarity check error: {e}")
            return self._overlap_similarity(text1, text2)
    
    def _overlap_similarity(self, text1: str, text2: str) -> float:
        """Simple Jaccard similarity fallback."""
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())
        if not words1 or not words2:
            return 0.0
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        return intersection / union if union > 0 else 0.0
    
    def _split_sentences(self, text: str) -> List[str]:
        """Split text into sentences."""
        sentences = re.split(r'(?<=[.!?])\s+', text)
        return [s.strip() for s in sentences if len(s.strip()) > 10]
