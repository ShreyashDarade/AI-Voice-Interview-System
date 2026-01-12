import { useState, useEffect, useRef } from 'react';
import PropTypes from 'prop-types';
import { createInterviewWebSocket, endInterview, reportCheating } from '../services/api';

export default function InterviewPage({ resumeData, onComplete }) {
  const [status, setStatus] = useState('connecting');
  const [strikes, setStrikes] = useState(0);
  const [warning, setWarning] = useState(null);
  const [transcript, setTranscript] = useState([]);
  const [isRecording, setIsRecording] = useState(false);
  const [terminated, setTerminated] = useState(false);

  const videoRef = useRef(null);
  const wsRef = useRef(null);
  const mediaRecorderRef = useRef(null);
  const audioContextRef = useRef(null);
  const streamRef = useRef(null);

  const { interview, resume } = resumeData;

  // Initialize webcam and WebSocket
  useEffect(() => {
    initializeMedia();
    connectWebSocket();

    return () => {
      cleanup();
    };
  }, []);

  const initializeMedia = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: true,
        audio: true,
      });

      streamRef.current = stream;

      if (videoRef.current) {
        videoRef.current.srcObject = stream;
      }

      // Setup audio context for processing
      audioContextRef.current = new AudioContext({ sampleRate: 16000 });

    } catch (err) {
      console.error('Failed to access media devices:', err);
      setStatus('error');
    }
  };

  const connectWebSocket = () => {
    wsRef.current = createInterviewWebSocket(interview.id, {
      onConnected: () => {
        setStatus('connected');
        startAudioCapture();
      },
      onTranscript: (text) => {
        setTranscript((prev) => [...prev, { type: 'ai', text }]);
      },
      onAudio: async (blob) => {
        // Convert Blob to ArrayBuffer for raw PCM playback
        const arrayBuffer = await blob.arrayBuffer();
        playAudio(arrayBuffer);
      },
      onWarning: (data) => {
        setStrikes(data.strikes);
        setWarning(data.message);
        setTimeout(() => setWarning(null), 5000);
      },
      onTerminated: (data) => {
        setTerminated(true);
        setStrikes(data.strikes);
        handleInterviewEnd(true);
      },
      onError: (msg) => {
        console.error('WebSocket error:', msg);
      },
      onDisconnected: () => {
        setStatus('disconnected');
      },
    });
  };

  const startAudioCapture = () => {
    if (!streamRef.current || !audioContextRef.current) {
      console.error('Cannot start audio capture: missing stream or context');
      return;
    }

    const audioContext = audioContextRef.current;
    const source = audioContext.createMediaStreamSource(streamRef.current);

    // Create a ScriptProcessor for raw audio capture (16kHz Float32)
    // Using 4096 buffer size for ~256ms chunks
    const scriptProcessor = audioContext.createScriptProcessor(4096, 1, 1);

    scriptProcessor.onaudioprocess = (event) => {
      if (!wsRef.current) return;

      const inputData = event.inputBuffer.getChannelData(0);

      // Convert Float32Array to bytes
      const float32Array = new Float32Array(inputData);
      const buffer = float32Array.buffer;

      // Send raw Float32 PCM to server
      wsRef.current.sendAudio(buffer);
    };

    // Connect: source -> processor -> destination (required for processing)
    // IMPORTANT: Keep the source in the ref to prevent garbage collection
    source.connect(scriptProcessor);
    scriptProcessor.connect(audioContext.destination);

    // Store BOTH references for cleanup and to prevent GC
    mediaRecorderRef.current = {
      processor: scriptProcessor,
      source: source,
      disconnect: () => {
        scriptProcessor.disconnect();
        source.disconnect();
      }
    };

    setIsRecording(true);
    console.log('Audio capture started successfully');
  };

  // Audio queue system for smooth playback
  const nextStartTimeRef = useRef(0);

  const playAudio = async (arrayBuffer) => {
    try {
      // Gemini sends 24kHz PCM16 audio as raw bytes
      const audioContext = audioContextRef.current || new AudioContext({ sampleRate: 24000 });
      audioContextRef.current = audioContext;

      // Convert ArrayBuffer to AudioBuffer
      // The data is raw PCM16, need to convert to Float32 for Web Audio API
      const pcm16Data = new Int16Array(arrayBuffer);
      const float32Data = new Float32Array(pcm16Data.length);

      // Convert Int16 to Float32 (-1.0 to 1.0)
      for (let i = 0; i < pcm16Data.length; i++) {
        float32Data[i] = pcm16Data[i] / 32768;
      }

      // Create AudioBuffer
      const audioBuffer = audioContext.createBuffer(1, float32Data.length, 24000);
      audioBuffer.getChannelData(0).set(float32Data);

      // Schedule for playback (buffered queue system)
      const currentTime = audioContext.currentTime;
      const startTime = Math.max(currentTime, nextStartTimeRef.current);

      const source = audioContext.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(audioContext.destination);
      source.start(startTime);

      // Update next start time to avoid overlapping
      nextStartTimeRef.current = startTime + audioBuffer.duration;

      // Clean up old scheduled times
      if (nextStartTimeRef.current < currentTime) {
        nextStartTimeRef.current = currentTime;
      }

    } catch (err) {
      console.error('Failed to play audio:', err);
    }
  };

  // Comprehensive Cheating Detection System
  const tabSwitchCountRef = useRef(0);
  const lastFaceDetectedRef = useRef(Date.now());
  const faceDetectorRef = useRef(null);

  // Initialize Face Detector if available
  useEffect(() => {
    const initFaceDetector = async () => {
      if ('FaceDetector' in globalThis) {
        try {
          const FaceDetector = globalThis.FaceDetector;
          faceDetectorRef.current = new FaceDetector({ fastMode: true });
          console.log('Face detection initialized');
        } catch (err) {
          console.warn('Face detector not available:', err);
        }
      } else {
        console.warn('FaceDetector API not available in this browser');
      }
    };
    initFaceDetector();
  }, []);

  // 1. TAB VISIBILITY DETECTION - Strict (immediate strike for switching tabs)
  useEffect(() => {
    const handleVisibilityChange = () => {
      if (document.hidden && !terminated) {
        console.warn('CHEATING: Tab switched or window minimized');
        tabSwitchCountRef.current += 1;
        handleCheatingDetection('tab_switch', 1, {
          message: 'Tab switching detected',
          timestamp: new Date().toISOString()
        });
      }
    };

    const handleBlur = () => {
      if (!terminated) {
        console.warn('CHEATING: Window lost focus');
        handleCheatingDetection('window_blur', 0.85, {
          message: 'Window focus lost',
          timestamp: new Date().toISOString()
        });
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    window.addEventListener('blur', handleBlur);

    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      window.removeEventListener('blur', handleBlur);
    };
  }, [terminated]);

  // 2. FACE DETECTION - Check for face presence
  useEffect(() => {
    const detectFace = async () => {
      if (!videoRef.current || !faceDetectorRef.current || terminated) return;

      try {
        const videoElement = videoRef.current;

        // Create canvas to capture frame
        const canvas = document.createElement('canvas');
        canvas.width = videoElement.videoWidth || 640;
        canvas.height = videoElement.videoHeight || 480;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(videoElement, 0, 0);

        // Detect faces
        const faces = await faceDetectorRef.current.detect(canvas);

        if (faces.length === 0) {
          // No face detected
          const timeSinceLastFace = Date.now() - lastFaceDetectedRef.current;
          if (timeSinceLastFace > 3000) { // No face for 3 seconds
            console.warn('CHEATING: No face detected');
            handleCheatingDetection('no_face', 0.8, {
              message: 'Face not visible',
              duration: timeSinceLastFace
            });
            lastFaceDetectedRef.current = Date.now(); // Reset to avoid spam
          }
        } else if (faces.length > 1) {
          // Multiple faces detected
          console.warn('CHEATING: Multiple faces detected');
          handleCheatingDetection('multiple_faces', 0.9, {
            message: 'Multiple people detected',
            count: faces.length
          });
        } else {
          // Face detected - update timestamp
          lastFaceDetectedRef.current = Date.now();
        }
      } catch (err) {
        // Face detection failed - continue silently
        console.debug('Face detection error:', err);
      }
    };

    const faceCheckInterval = setInterval(detectFace, 2000); // Check every 2 seconds

    return () => clearInterval(faceCheckInterval);
  }, [terminated]);

  // 3. RIGHT-CLICK AND COPY DETECTION
  useEffect(() => {
    const handleContextMenu = (e) => {
      if (!terminated) {
        e.preventDefault();
        console.warn('CHEATING: Right-click attempt');
        handleCheatingDetection('right_click', 0.65, {
          message: 'Right-click detected'
        });
      }
    };

    const handleCopy = (e) => {
      if (!terminated) {
        e.preventDefault();
        console.warn('CHEATING: Copy attempt');
        handleCheatingDetection('copy_attempt', 0.7, {
          message: 'Copy/paste detected'
        });
      }
    };

    document.addEventListener('contextmenu', handleContextMenu);
    document.addEventListener('copy', handleCopy);

    return () => {
      document.removeEventListener('contextmenu', handleContextMenu);
      document.removeEventListener('copy', handleCopy);
    };
  }, [terminated]);

  const handleCheatingDetection = async (eventType = 'looking_away', confidence = 0.85, details = {}) => {
    try {
      console.log(`Reporting cheating: ${eventType} (confidence: ${confidence})`);
      const result = await reportCheating(interview.id, eventType, confidence, details);

      if (result.strikes) {
        setStrikes(result.strikes);

        // Show strike notification
        const strikeMessage = result.warning_message || `Strike ${result.strikes}/2: ${eventType.replace('_', ' ')}`;
        setWarning(strikeMessage);
        setTimeout(() => setWarning(null), 5000);
      }

      if (result.terminated) {
        setTerminated(true);
        handleInterviewEnd(true);
      }
    } catch (err) {
      console.error('Cheating report error:', err);
    }
  };

  const handleInterviewEnd = async (wasTerminated = false) => {
    cleanup();

    try {
      const result = await endInterview(interview.id);
      onComplete({
        ...result,
        wasTerminated,
        strikes,
      });
    } catch (err) {
      console.error('Failed to end interview:', err);
      onComplete({
        interview,
        wasTerminated,
        strikes,
      });
    }
  };

  const cleanup = () => {
    // Disconnect ScriptProcessor
    if (mediaRecorderRef.current) {
      try {
        mediaRecorderRef.current.disconnect();
      } catch {
        // ScriptProcessor may already be disconnected
      }
    }
    if (wsRef.current) {
      wsRef.current.close();
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
    }
    if (audioContextRef.current) {
      audioContextRef.current.close();
    }
  };

  return (
    <div>
      {/* Warning Toast */}
      {warning && (
        <div className={`toast ${strikes >= 1 ? 'toast-danger' : 'toast-warning'}`}>
          {warning}
        </div>
      )}

      {/* Terminated Modal */}
      {terminated && (
        <div style={{
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          background: 'rgba(0,0,0,0.8)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 1000,
        }}>
          <div className="card" style={{ maxWidth: '500px', textAlign: 'center' }}>
            <div style={{ fontSize: '4rem', marginBottom: '1rem' }}>🚫</div>
            <h2 style={{ color: 'var(--danger)' }}>Interview Terminated</h2>
            <p style={{ margin: '1rem 0', color: 'rgba(255,255,255,0.7)' }}>
              This interview has been terminated due to suspected cheating.
              The session has been flagged for review.
            </p>
            <button className="btn btn-primary" onClick={() => onComplete({ terminated: true, strikes })}>
              Return to Home
            </button>
          </div>
        </div>
      )}

      <div className="grid grid-2" style={{ gap: '2rem' }}>
        {/* Video Feed */}
        <div className="card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
            <h3>Your Camera</h3>
            <div className="strike-counter">
              <span>Strikes:</span>
              {[1, 2].map((num) => (
                <div key={num} className={`strike ${strikes >= num ? 'active' : ''}`}>
                  {strikes >= num ? '✕' : ''}
                </div>
              ))}
            </div>
          </div>

          <div className="video-container">
            <video
              ref={videoRef}
              autoPlay
              muted
              playsInline
              style={{ transform: 'scaleX(-1)' }}
            />

            {/* Status Overlay */}
            <div style={{
              position: 'absolute',
              top: '1rem',
              left: '1rem',
              display: 'flex',
              gap: '0.5rem',
            }}>
              <span className={`badge ${status === 'connected' ? 'badge-success' : 'badge-warning'}`}>
                {status === 'connected' ? '● Connected' : '○ ' + status}
              </span>
              {isRecording && (
                <span className="badge badge-danger pulse">● Recording</span>
              )}
            </div>
          </div>
        </div>

        {/* Interview Info */}
        <div className="card">
          <h3 style={{ marginBottom: '1rem' }}>Interview Session</h3>

          <div style={{ marginBottom: '1.5rem' }}>
            <p><strong>Candidate:</strong> {resume.candidate_name}</p>
            <p><strong>Level:</strong> {interview.experience_level}</p>
            <p><strong>Status:</strong> <span className="badge badge-success">{interview.status}</span></p>
          </div>

          {/* Voice Indicator */}
          <div style={{ marginBottom: '1.5rem' }}>
            <strong>AI Voice Status</strong>
            <div className="voice-indicator" style={{ marginTop: '0.5rem' }}>
              <div className="bar"></div>
              <div className="bar"></div>
              <div className="bar"></div>
              <div className="bar"></div>
              <div className="bar"></div>
              <span style={{ marginLeft: '0.5rem' }}>Listening...</span>
            </div>
          </div>

          {/* Transcript */}
          <div>
            <strong>Transcript</strong>
            <div className="transcript" style={{ marginTop: '0.5rem' }}>
              {transcript.length === 0 ? (
                <p style={{ color: 'rgba(255,255,255,0.5)' }}>
                  Waiting for AI to speak...
                </p>
              ) : (
                transcript.map((line) => (
                  <div key={`${line.type}-${line.text.substring(0, 20)}`} className={`transcript-line ${line.type}`}>
                    <strong>{line.type === 'ai' ? '🤖 AI:' : '👤 You:'}</strong> {line.text}
                  </div>
                ))
              )}
            </div>
          </div>

          <button
            className="btn btn-danger"
            onClick={() => handleInterviewEnd(false)}
            style={{ width: '100%', marginTop: '1.5rem' }}
          >
            End Interview
          </button>
        </div>
      </div>

      {/* Instructions */}
      <div className="card" style={{ marginTop: '2rem' }}>
        <h3>⚠️ STRICT Anti-Cheating Rules</h3>
        <ul style={{ marginTop: '1rem', paddingLeft: '1.5rem', color: 'rgba(255,255,255,0.7)' }}>
          <li><strong>Keep your face visible</strong> in the camera at all times</li>
          <li><strong>Look at the screen</strong> - looking away triggers detection</li>
          <li><strong>DO NOT switch tabs</strong> or minimize window (instant strike)</li>
          <li><strong>NO right-clicking</strong> or copy/paste allowed</li>
          <li><strong>Stay alone</strong> - multiple faces will be detected</li>
          <li><strong>Only 2 strikes allowed</strong> before automatic termination</li>
          <li>All violations are logged and flagged for review</li>
        </ul>
      </div>
    </div>
  );
}

InterviewPage.propTypes = {
  resumeData: PropTypes.shape({
    interview: PropTypes.shape({
      id: PropTypes.string.isRequired,
      experience_level: PropTypes.string,
      status: PropTypes.string,
    }).isRequired,
    resume: PropTypes.shape({
      candidate_name: PropTypes.string,
    }).isRequired,
  }).isRequired,
  onComplete: PropTypes.func.isRequired,
};
