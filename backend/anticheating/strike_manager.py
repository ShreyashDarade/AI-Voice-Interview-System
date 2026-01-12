"""
Strike manager for the 3-strike cheating detection system.
"""
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
from django.conf import settings


@dataclass
class Strike:
    """Represents a single strike."""
    number: int
    event_type: str
    confidence: float
    timestamp: datetime
    details: Dict[str, Any]


@dataclass
class StrikeResult:
    """Result of processing a potential strike."""
    strike_added: bool
    current_strikes: int
    max_strikes: int
    should_terminate: bool
    warning_message: str
    strike: Optional[Strike]


class StrikeManager:
    """
    Manages the 3-strike system for cheating detection.
    
    Strike 1: Warning
    Strike 2: Final Warning
    Strike 3: Termination
    """
    
    WARNING_MESSAGES = {
        1: "⚠️ Warning: Please keep your eyes focused on the screen during the interview.",
        2: "⚠️ FINAL WARNING: One more violation will result in interview termination.",
        3: "🚫 Interview terminated due to suspected cheating. This session has been flagged for review."
    }
    
    def __init__(self, interview_id: str, max_strikes: int = None):
        """
        Initialize strike manager.
        
        Args:
            interview_id: ID of the interview session
            max_strikes: Maximum strikes before termination (default from settings)
        """
        self.interview_id = interview_id
        self.max_strikes = max_strikes or settings.MAX_STRIKES
        self.strikes: list[Strike] = []
        self.created_at = datetime.now()
    
    @property
    def current_strikes(self) -> int:
        """Get current strike count."""
        return len(self.strikes)
    
    @property
    def should_terminate(self) -> bool:
        """Check if interview should be terminated."""
        return self.current_strikes >= self.max_strikes
    
    def process_violation(
        self,
        event_type: str,
        confidence: float,
        details: Dict[str, Any] = None
    ) -> StrikeResult:
        """
        Process a potential cheating violation.
        
        Args:
            event_type: Type of cheating event
            confidence: Detection confidence (0-1)
            details: Additional event details
            
        Returns:
            StrikeResult with action taken
        """
        # Check if confidence meets threshold
        if confidence < settings.CHEATING_CONFIDENCE_THRESHOLD:
            return StrikeResult(
                strike_added=False,
                current_strikes=self.current_strikes,
                max_strikes=self.max_strikes,
                should_terminate=False,
                warning_message="",
                strike=None
            )
        
        # Add strike
        strike = Strike(
            number=self.current_strikes + 1,
            event_type=event_type,
            confidence=confidence,
            timestamp=datetime.now(),
            details=details or {}
        )
        self.strikes.append(strike)
        
        # Get warning message
        warning = self.WARNING_MESSAGES.get(
            self.current_strikes,
            f"Strike {self.current_strikes} issued."
        )
        
        return StrikeResult(
            strike_added=True,
            current_strikes=self.current_strikes,
            max_strikes=self.max_strikes,
            should_terminate=self.should_terminate,
            warning_message=warning,
            strike=strike
        )
    
    def get_termination_reason(self) -> str:
        """Get the termination reason message."""
        if not self.should_terminate:
            return ""
        
        events = [s.event_type for s in self.strikes]
        return (
            f"Interview terminated after {self.max_strikes} cheating violations. "
            f"Detected events: {', '.join(events)}. "
            f"This session has been flagged for manual review."
        )
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all strikes."""
        return {
            "interview_id": self.interview_id,
            "total_strikes": self.current_strikes,
            "max_strikes": self.max_strikes,
            "terminated": self.should_terminate,
            "strikes": [
                {
                    "number": s.number,
                    "event_type": s.event_type,
                    "confidence": s.confidence,
                    "timestamp": s.timestamp.isoformat()
                }
                for s in self.strikes
            ],
            "duration_seconds": (datetime.now() - self.created_at).total_seconds()
        }
    
    def reset(self):
        """Reset all strikes (use with caution)."""
        self.strikes = []
    
    def remove_last_strike(self) -> bool:
        """
        Remove the last strike (for appeals/corrections).
        
        Returns:
            True if a strike was removed, False if no strikes to remove
        """
        if self.strikes:
            self.strikes.pop()
            return True
        return False
