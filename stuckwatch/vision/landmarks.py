"""MediaPipe Face Mesh wrapper for facial landmark extraction.

Extracts normalized 3D landmarks (including iris landmarks 468-477).
Enforces privacy: raw image frames are processed in memory and immediately dereferenced.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from stuckwatch.logging_setup import get_logger

logger = get_logger("vision.landmarks")

try:
    import mediapipe as mp
except ImportError:
    mp = None  # type: ignore


@dataclass
class FaceLandmarks:
    """Normalized landmark array for a single detected face.

    Shape: (N, 3) where columns are (x, y, z) normalized coordinates.
    N is 478 when iris landmarks are enabled.
    """
    points: np.ndarray

    def get_pt(self, idx: int) -> np.ndarray:
        """Return 2D or 3D point array for a given landmark index."""
        return self.points[idx]


class LandmarkDetector:
    """Wrapper around MediaPipe FaceMesh."""

    def __init__(self) -> None:
        self._mesh = None
        self._initialized = False

    def _ensure_initialized(self) -> bool:
        if self._initialized:
            return self._mesh is not None

        if mp is None:
            logger.error("MediaPipe is not installed. Face mesh detection unavailable.")
            self._initialized = True
            return False

        try:
            mp_face_mesh = mp.solutions.face_mesh
            self._mesh = mp_face_mesh.FaceMesh(
                max_num_faces=1,
                refine_landmarks=True,  # Enables iris landmarks 468-477
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5,
                static_image_mode=False,
            )
            self._initialized = True
            logger.info("MediaPipe FaceMesh initialized successfully.")
            return True
        except Exception as exc:
            logger.error("Failed to initialize MediaPipe FaceMesh: %s", exc)
            self._initialized = True
            return False

    def process_frame(self, frame) -> Optional[FaceLandmarks]:
        """Detect facial landmarks in a BGR frame and immediately discard the frame.

        Args:
            frame: OpenCV BGR image array.

        Returns:
            FaceLandmarks or None if no face detected or frame is invalid.
        """
        if frame is None or not self._ensure_initialized() or self._mesh is None:
            if frame is not None:
                del frame
            return None

        landmarks_obj: Optional[FaceLandmarks] = None
        try:
            # MediaPipe expects RGB
            rgb_frame = frame[:, :, ::-1]
            results = self._mesh.process(rgb_frame)

            if results.multi_face_landmarks:
                raw_landmarks = results.multi_face_landmarks[0].landmark
                # Convert to numpy array of shape (N, 3)
                pts = np.array(
                    [[lm.x, lm.y, lm.z] for lm in raw_landmarks], dtype=np.float32
                )
                landmarks_obj = FaceLandmarks(points=pts)
        except Exception as exc:
            logger.debug("Error during landmark processing: %s", exc)
        finally:
            # Privacy guarantee: explicitly delete raw frame reference
            del frame

        return landmarks_obj

    def close(self) -> None:
        """Release underlying MediaPipe graph."""
        if self._mesh is not None:
            try:
                self._mesh.close()
            except Exception:
                pass
            self._mesh = None
        self._initialized = False
