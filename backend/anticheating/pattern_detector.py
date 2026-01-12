"""
ANN-based pattern detector for suspicious behavior analysis.
Analyzes sequences of eye tracking data to detect cheating patterns.
"""
import numpy as np
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from collections import deque
from datetime import datetime, timedelta


@dataclass
class PatternAnalysisResult:
    """Result of pattern analysis."""
    is_suspicious: bool
    confidence: float
    pattern_type: str
    description: str
    details: Dict[str, Any]


class PatternDetector:
    """
    ANN-based pattern recognition for detecting suspicious behavior.
    
    Analyzes temporal patterns in eye tracking data to identify:
    1. Frequent looking away (reading from another screen)
    2. Regular intervals (scripted behavior)
    3. Sudden changes in behavior
    4. Consistent off-screen glances
    """
    
    # Pattern detection thresholds
    LOOKAWAY_FREQUENCY_THRESHOLD = 0.4  # 40% of samples looking away
    REGULAR_INTERVAL_THRESHOLD = 0.15  # 15% deviation = regular pattern
    MIN_SAMPLES_FOR_ANALYSIS = 10
    WINDOW_SIZE = 30  # Analyze last 30 samples
    
    def __init__(self):
        """Initialize pattern detector."""
        self.history: deque = deque(maxlen=100)  # Keep last 100 samples
        self.timestamps: deque = deque(maxlen=100)
        self.lookaway_events: List[datetime] = []
    
    def add_sample(self, is_looking_away: bool, confidence: float, timestamp: Optional[datetime] = None):
        """
        Add a new eye tracking sample.
        
        Args:
            is_looking_away: Whether the candidate was looking away
            confidence: Confidence of the detection
            timestamp: Optional timestamp (uses current time if not provided)
        """
        ts = timestamp or datetime.now()
        self.history.append({
            'looking_away': is_looking_away,
            'confidence': confidence,
            'timestamp': ts
        })
        self.timestamps.append(ts)
        
        if is_looking_away:
            self.lookaway_events.append(ts)
    
    def analyze(self) -> PatternAnalysisResult:
        """
        Analyze accumulated samples for suspicious patterns.
        
        Returns:
            PatternAnalysisResult with suspicion assessment
        """
        if len(self.history) < self.MIN_SAMPLES_FOR_ANALYSIS:
            return PatternAnalysisResult(
                is_suspicious=False,
                confidence=0.0,
                pattern_type="insufficient_data",
                description="Not enough samples for analysis",
                details={"samples": len(self.history)}
            )
        
        # Get recent window
        recent = list(self.history)[-self.WINDOW_SIZE:]
        
        # Check for frequent looking away
        freq_result = self._check_lookaway_frequency(recent)
        if freq_result.is_suspicious:
            return freq_result
        
        # Check for regular interval patterns
        interval_result = self._check_regular_intervals()
        if interval_result.is_suspicious:
            return interval_result
        
        # Check for sudden behavior changes
        change_result = self._check_behavior_change()
        if change_result.is_suspicious:
            return change_result
        
        return PatternAnalysisResult(
            is_suspicious=False,
            confidence=0.0,
            pattern_type="normal",
            description="No suspicious patterns detected",
            details={"samples_analyzed": len(recent)}
        )
    
    def _check_lookaway_frequency(self, samples: List[Dict]) -> PatternAnalysisResult:
        """Check for excessive looking away frequency."""
        lookaway_count = sum(1 for s in samples if s['looking_away'])
        frequency = lookaway_count / len(samples)
        
        if frequency > self.LOOKAWAY_FREQUENCY_THRESHOLD:
            return PatternAnalysisResult(
                is_suspicious=True,
                confidence=min(0.5 + frequency, 0.95),
                pattern_type="frequent_lookaway",
                description=f"Candidate looking away {frequency*100:.1f}% of the time",
                details={
                    "lookaway_count": lookaway_count,
                    "total_samples": len(samples),
                    "frequency": frequency
                }
            )
        
        return PatternAnalysisResult(
            is_suspicious=False,
            confidence=0.0,
            pattern_type="normal_frequency",
            description="Normal looking away frequency",
            details={"frequency": frequency}
        )
    
    def _check_regular_intervals(self) -> PatternAnalysisResult:
        """Check for suspiciously regular intervals between lookaway events."""
        if len(self.lookaway_events) < 5:
            return PatternAnalysisResult(
                is_suspicious=False,
                confidence=0.0,
                pattern_type="insufficient_events",
                description="Not enough lookaway events",
                details={}
            )
        
        # Calculate intervals between lookaway events
        recent_events = self.lookaway_events[-20:]
        intervals = []
        for i in range(1, len(recent_events)):
            delta = (recent_events[i] - recent_events[i-1]).total_seconds()
            if delta > 0:
                intervals.append(delta)
        
        if len(intervals) < 3:
            return PatternAnalysisResult(
                is_suspicious=False,
                confidence=0.0,
                pattern_type="insufficient_intervals",
                description="Not enough intervals to analyze",
                details={}
            )
        
        # Check for regularity (low standard deviation relative to mean)
        mean_interval = np.mean(intervals)
        std_interval = np.std(intervals)
        
        if mean_interval > 0:
            cv = std_interval / mean_interval  # Coefficient of variation
            
            if cv < self.REGULAR_INTERVAL_THRESHOLD:
                return PatternAnalysisResult(
                    is_suspicious=True,
                    confidence=0.85,
                    pattern_type="regular_pattern",
                    description=f"Suspiciously regular lookaway pattern (every ~{mean_interval:.1f}s)",
                    details={
                        "mean_interval": mean_interval,
                        "std_interval": std_interval,
                        "coefficient_of_variation": cv
                    }
                )
        
        return PatternAnalysisResult(
            is_suspicious=False,
            confidence=0.0,
            pattern_type="irregular_intervals",
            description="Normal irregular intervals",
            details={"mean_interval": mean_interval if intervals else 0}
        )
    
    def _check_behavior_change(self) -> PatternAnalysisResult:
        """Check for sudden changes in behavior patterns."""
        if len(self.history) < 20:
            return PatternAnalysisResult(
                is_suspicious=False,
                confidence=0.0,
                pattern_type="insufficient_history",
                description="Not enough history",
                details={}
            )
        
        samples = list(self.history)
        
        # Compare first half vs second half
        mid = len(samples) // 2
        first_half = samples[:mid]
        second_half = samples[mid:]
        
        first_lookaway_rate = sum(1 for s in first_half if s['looking_away']) / len(first_half)
        second_lookaway_rate = sum(1 for s in second_half if s['looking_away']) / len(second_half)
        
        change = abs(second_lookaway_rate - first_lookaway_rate)
        
        # Significant increase in looking away is suspicious
        if second_lookaway_rate > first_lookaway_rate and change > 0.3:
            return PatternAnalysisResult(
                is_suspicious=True,
                confidence=0.7 + change * 0.3,
                pattern_type="behavior_change",
                description="Sudden increase in looking away behavior",
                details={
                    "first_half_rate": first_lookaway_rate,
                    "second_half_rate": second_lookaway_rate,
                    "change": change
                }
            )
        
        return PatternAnalysisResult(
            is_suspicious=False,
            confidence=0.0,
            pattern_type="stable_behavior",
            description="Behavior pattern is stable",
            details={"change": change}
        )
    
    def reset(self):
        """Reset the pattern detector state."""
        self.history.clear()
        self.timestamps.clear()
        self.lookaway_events.clear()
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary statistics of the session."""
        if not self.history:
            return {"samples": 0}
        
        samples = list(self.history)
        lookaway_count = sum(1 for s in samples if s['looking_away'])
        
        return {
            "total_samples": len(samples),
            "lookaway_count": lookaway_count,
            "lookaway_rate": lookaway_count / len(samples),
            "session_duration": (
                (self.timestamps[-1] - self.timestamps[0]).total_seconds()
                if len(self.timestamps) > 1 else 0
            )
        }
