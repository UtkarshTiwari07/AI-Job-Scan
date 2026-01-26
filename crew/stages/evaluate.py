# Stage E: Evaluate & Monitor
# Metrics tracking and drift detection

"""
EVALUATE STAGE - Eighth/final step in HUMANIZE pipeline
Purpose: Track metrics, enable monitoring, detect drift over time
"""

from datetime import datetime
from typing import Dict, Any, Optional
import json


class EvaluateStage:
    """
    Automated evaluation and monitoring for the HUMANIZE pipeline.
    
    Tracks:
    - Human scores over time
    - Semantic similarity averages
    - Failure rates
    - Processing times
    """
    
    def __init__(self, log_file: Optional[str] = None):
        self.log_file = log_file
        self.session_metrics = []
    
    def evaluate(self, original: str, humanized: str,
                 classification: Dict, similarity: float,
                 processing_time: float = 0.0) -> Dict:
        """
        Compile final evaluation metrics.
        
        Args:
            original: Original text
            humanized: Humanized text
            classification: Results from Zoom stage
            similarity: Semantic similarity from Narrow stage
            processing_time: Total pipeline processing time
            
        Returns:
            Complete metrics dictionary
        """
        orig_words = len(original.split())
        new_words = len(humanized.split())
        
        metrics = {
            "human_score": classification.get("human_score", 0),
            "semantic_similarity": round(similarity, 3),
            "passed_all_checks": (
                classification.get("passed", False) and 
                similarity >= 0.75
            ),
            "issues_count": len(classification.get("issues", [])),
            "issues": classification.get("issues", []),
            "length_change": {
                "original_words": orig_words,
                "humanized_words": new_words,
                "ratio": round(new_words / max(orig_words, 1), 2)
            },
            "burstiness_cv": classification.get("details", {}).get("burstiness_cv", 0),
            "processing_time_ms": round(processing_time * 1000, 2),
            "timestamp": datetime.now().isoformat(),
            "version": "2.0.0"  # HUMANIZE pipeline version
        }
        
        # Store for session tracking
        self.session_metrics.append(metrics)
        
        # Log if file specified
        if self.log_file:
            self._log_metrics(metrics)
        
        return metrics
    
    def get_session_summary(self) -> Dict:
        """Get summary of all evaluations in this session."""
        if not self.session_metrics:
            return {"count": 0, "message": "No evaluations yet"}
        
        scores = [m["human_score"] for m in self.session_metrics]
        similarities = [m["semantic_similarity"] for m in self.session_metrics]
        passed = [m["passed_all_checks"] for m in self.session_metrics]
        
        return {
            "count": len(self.session_metrics),
            "average_human_score": round(sum(scores) / len(scores), 1),
            "average_similarity": round(sum(similarities) / len(similarities), 3),
            "pass_rate": round(sum(passed) / len(passed) * 100, 1),
            "min_score": min(scores),
            "max_score": max(scores),
        }
    
    def check_for_drift(self, window_size: int = 10) -> Dict:
        """
        Check if recent metrics show drift from expected performance.
        
        Args:
            window_size: Number of recent evaluations to consider
            
        Returns:
            Drift analysis with alerts
        """
        if len(self.session_metrics) < window_size:
            return {"status": "insufficient_data", "message": f"Need {window_size} samples"}
        
        recent = self.session_metrics[-window_size:]
        earlier = self.session_metrics[:-window_size][-window_size:] if len(self.session_metrics) > window_size else []
        
        recent_avg = sum(m["human_score"] for m in recent) / len(recent)
        
        alerts = []
        
        # Alert if average score drops
        if earlier:
            earlier_avg = sum(m["human_score"] for m in earlier) / len(earlier)
            if recent_avg < earlier_avg - 10:
                alerts.append({
                    "type": "score_drop",
                    "message": f"Average score dropped from {earlier_avg:.1f} to {recent_avg:.1f}"
                })
        
        # Alert if failure rate is high
        recent_failures = sum(1 for m in recent if not m["passed_all_checks"])
        failure_rate = recent_failures / len(recent)
        if failure_rate > 0.3:
            alerts.append({
                "type": "high_failure_rate",
                "message": f"Failure rate is {failure_rate*100:.1f}%"
            })
        
        # Alert if similarity is dropping
        recent_sim = sum(m["semantic_similarity"] for m in recent) / len(recent)
        if recent_sim < 0.75:
            alerts.append({
                "type": "low_similarity",
                "message": f"Average similarity is low ({recent_sim:.2f})"
            })
        
        return {
            "status": "alerts" if alerts else "healthy",
            "alerts": alerts,
            "recent_average_score": round(recent_avg, 1),
            "sample_count": len(recent)
        }
    
    def _log_metrics(self, metrics: Dict):
        """Append metrics to log file."""
        try:
            with open(self.log_file, 'a') as f:
                f.write(json.dumps(metrics) + '\n')
        except Exception as e:
            print(f"Warning: Could not log metrics: {e}")
    
    def generate_report(self) -> str:
        """Generate a human-readable report of session metrics."""
        summary = self.get_session_summary()
        drift = self.check_for_drift()
        
        report = []
        report.append("=" * 50)
        report.append("HUMANIZE Pipeline - Session Report")
        report.append("=" * 50)
        report.append(f"Processed: {summary.get('count', 0)} texts")
        report.append(f"Average Human Score: {summary.get('average_human_score', 0)}/100")
        report.append(f"Average Similarity: {summary.get('average_similarity', 0)}")
        report.append(f"Pass Rate: {summary.get('pass_rate', 0)}%")
        report.append(f"Score Range: {summary.get('min_score', 0)} - {summary.get('max_score', 0)}")
        report.append("")
        
        if drift.get("alerts"):
            report.append("⚠️ ALERTS:")
            for alert in drift["alerts"]:
                report.append(f"  - {alert['type']}: {alert['message']}")
        else:
            report.append("✅ Status: Healthy")
        
        report.append("=" * 50)
        
        return "\n".join(report)
