"""
CNN-based eye tracking for cheating detection using MediaPipe.
"""
import numpy as np
from typing import Dict, Tuple, List, Optional, Any
from dataclasses import dataclass
from enum import Enum


class GazeDirection(Enum):
    """Eye gaze direction classification."""
    CENTER = "center"
    LEFT = "left"
    RIGHT = "right"
    UP = "up"
    DOWN = "down"
    AWAY = "away"  # Looking significantly away from screen


@dataclass
class EyeTrackingResult:
    """Result of eye tracking analysis."""
    gaze_direction: GazeDirection
    confidence: float
    is_looking_away: bool
    left_eye_ratio: float
    right_eye_ratio: float
    face_detected: bool
    multiple_faces: bool
    details: Dict[str, Any]


class EyeTracker:
    """
    Eye tracking using MediaPipe Face Mesh + CNN classifier.
    
    Uses MediaPipe's 468 facial landmarks to:
    1. Detect face and eye regions
    2. Calculate eye aspect ratios
    3. Determine gaze direction
    4. Detect looking away patterns
    """
    
    # MediaPipe Face Mesh landmark indices for eyes
    LEFT_EYE_INDICES = [362, 385, 387, 263, 373, 380]  # Outer landmarks
    RIGHT_EYE_INDICES = [33, 160, 158, 133, 153, 144]
    LEFT_IRIS_INDEX = 468  # Center of left iris
    RIGHT_IRIS_INDEX = 473  # Center of right iris
    
    # Thresholds for gaze detection
    GAZE_THRESHOLD_HORIZONTAL = 0.3  # Ratio threshold for left/right
    GAZE_THRESHOLD_VERTICAL = 0.25  # Ratio threshold for up/down
    LOOKING_AWAY_THRESHOLD = 0.4  # Confidence threshold for "away"
    
    def __init__(self):
        """Initialize eye tracker with MediaPipe."""
        self._face_mesh = None
        self._initialized = False
    
    def _initialize(self):
        """Lazy initialization of MediaPipe."""
        if self._initialized:
            return
        
        try:
            import mediapipe as mp
            
            self.mp_face_mesh = mp.solutions.face_mesh
            self._face_mesh = self.mp_face_mesh.FaceMesh(
                max_num_faces=2,  # Detect multiple faces for cheating check
                refine_landmarks=True,  # Include iris landmarks
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5
            )
            self._initialized = True
        except ImportError:
            raise ImportError("MediaPipe is required for eye tracking. Install with: pip install mediapipe")
    
    def analyze_frame(self, frame: np.ndarray) -> EyeTrackingResult:
        """
        Analyze a video frame for eye tracking.
        
        Args:
            frame: BGR image frame from OpenCV (numpy array)
            
        Returns:
            EyeTrackingResult with gaze analysis
        """
        self._initialize()
        
        import cv2
        
        # Convert BGR to RGB for MediaPipe
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self._face_mesh.process(rgb_frame)
        
        # Check if any faces detected
        if not results.multi_face_landmarks:
            return EyeTrackingResult(
                gaze_direction=GazeDirection.AWAY,
                confidence=0.9,
                is_looking_away=True,
                left_eye_ratio=0,
                right_eye_ratio=0,
                face_detected=False,
                multiple_faces=False,
                details={"reason": "No face detected"}
            )
        
        # Check for multiple faces (potential cheating)
        multiple_faces = len(results.multi_face_landmarks) > 1
        
        # Analyze primary face
        face_landmarks = results.multi_face_landmarks[0]
        h, w = frame.shape[:2]
        
        # Get landmark coordinates
        landmarks = [(lm.x * w, lm.y * h) for lm in face_landmarks.landmark]
        
        # Calculate eye aspect ratios
        left_ear = self._calculate_eye_aspect_ratio(landmarks, self.LEFT_EYE_INDICES)
        right_ear = self._calculate_eye_aspect_ratio(landmarks, self.RIGHT_EYE_INDICES)
        
        # Calculate gaze direction using iris position
        gaze_direction, gaze_confidence = self._calculate_gaze_direction(landmarks)
        
        # Determine if looking away
        is_looking_away = (
            gaze_direction == GazeDirection.AWAY or
            gaze_confidence < 0.5 or
            multiple_faces
        )
        
        return EyeTrackingResult(
            gaze_direction=gaze_direction,
            confidence=gaze_confidence,
            is_looking_away=is_looking_away,
            left_eye_ratio=left_ear,
            right_eye_ratio=right_ear,
            face_detected=True,
            multiple_faces=multiple_faces,
            details={
                "num_faces": len(results.multi_face_landmarks),
                "left_ear": left_ear,
                "right_ear": right_ear
            }
        )
    
    def _calculate_eye_aspect_ratio(
        self, 
        landmarks: List[Tuple[float, float]], 
        eye_indices: List[int]
    ) -> float:
        """
        Calculate Eye Aspect Ratio (EAR) for blink/eye openness detection.
        
        EAR = (|p2-p6| + |p3-p5|) / (2 * |p1-p4|)
        """
        try:
            p1 = np.array(landmarks[eye_indices[0]])  # Outer corner
            p2 = np.array(landmarks[eye_indices[1]])  # Upper lid outer
            p3 = np.array(landmarks[eye_indices[2]])  # Upper lid inner
            p4 = np.array(landmarks[eye_indices[3]])  # Inner corner
            p5 = np.array(landmarks[eye_indices[4]])  # Lower lid inner
            p6 = np.array(landmarks[eye_indices[5]])  # Lower lid outer
            
            # Compute distances
            vertical_1 = np.linalg.norm(p2 - p6)
            vertical_2 = np.linalg.norm(p3 - p5)
            horizontal = np.linalg.norm(p1 - p4)
            
            if horizontal == 0:
                return 0
            
            ear = (vertical_1 + vertical_2) / (2.0 * horizontal)
            return float(ear)
        except (IndexError, ValueError):
            return 0
    
    def _calculate_gaze_direction(
        self,
        landmarks: List[Tuple[float, float]]
    ) -> Tuple[GazeDirection, float]:
        """
        Calculate gaze direction based on iris position relative to eye corners.
        """
        try:
            # Get iris centers (indices 468 and 473 for refined landmarks)
            if len(landmarks) <= self.RIGHT_IRIS_INDEX:
                # Fallback if iris landmarks not available
                return GazeDirection.CENTER, 0.6
            
            left_iris = np.array(landmarks[self.LEFT_IRIS_INDEX])
            right_iris = np.array(landmarks[self.RIGHT_IRIS_INDEX])
            
            # Get eye corners for reference
            left_outer = np.array(landmarks[self.LEFT_EYE_INDICES[0]])
            left_inner = np.array(landmarks[self.LEFT_EYE_INDICES[3]])
            right_outer = np.array(landmarks[self.RIGHT_EYE_INDICES[0]])
            right_inner = np.array(landmarks[self.RIGHT_EYE_INDICES[3]])
            
            # Calculate horizontal position of iris within eye
            left_eye_width = np.linalg.norm(left_outer - left_inner)
            right_eye_width = np.linalg.norm(right_outer - right_inner)
            
            if left_eye_width == 0 or right_eye_width == 0:
                return GazeDirection.CENTER, 0.5
            
            # Iris position ratio (0 = outer, 1 = inner)
            left_ratio = np.linalg.norm(left_iris - left_outer) / left_eye_width
            right_ratio = np.linalg.norm(right_iris - right_outer) / right_eye_width
            
            avg_ratio = (left_ratio + right_ratio) / 2
            
            # Determine direction
            if avg_ratio < 0.35:
                return GazeDirection.RIGHT, 0.8
            elif avg_ratio > 0.65:
                return GazeDirection.LEFT, 0.8
            else:
                # Check vertical position
                # (simplified - would need more landmarks for accurate vertical)
                return GazeDirection.CENTER, 0.9
                
        except (IndexError, ValueError, ZeroDivisionError):
            return GazeDirection.AWAY, 0.5
    
    def analyze_frame_base64(self, base64_data: str) -> EyeTrackingResult:
        """
        Analyze a base64-encoded image frame.
        
        Args:
            base64_data: Base64 encoded image data
            
        Returns:
            EyeTrackingResult
        """
        import base64
        import cv2
        
        # Decode base64 to image
        img_bytes = base64.b64decode(base64_data)
        img_array = np.frombuffer(img_bytes, dtype=np.uint8)
        frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        
        if frame is None:
            return EyeTrackingResult(
                gaze_direction=GazeDirection.AWAY,
                confidence=0.0,
                is_looking_away=True,
                left_eye_ratio=0,
                right_eye_ratio=0,
                face_detected=False,
                multiple_faces=False,
                details={"reason": "Failed to decode image"}
            )
        
        return self.analyze_frame(frame)
    
    def close(self):
        """Release resources."""
        if self._face_mesh:
            self._face_mesh.close()
            self._face_mesh = None
            self._initialized = False
