/**
 * API service for AI Interviewer backend.
 */

const API_BASE = 'http://localhost:8000/api';
const WS_BASE = 'ws://localhost:8000/ws';

/**
 * Upload a resume file.
 */
export async function uploadResume(file) {
  const formData = new FormData();
  formData.append('file', file);

  const response = await fetch(`${API_BASE}/resume/upload/`, {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error || 'Failed to upload resume');
  }

  return response.json();
}

/**
 * Get resume details.
 */
export async function getResume(resumeId) {
  const response = await fetch(`${API_BASE}/resume/${resumeId}/`);
  
  if (!response.ok) {
    throw new Error('Resume not found');
  }

  return response.json();
}

/**
 * Start a new interview.
 */
export async function startInterview(resumeId, experienceLevel) {
  const response = await fetch(`${API_BASE}/interview/start/`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      resume_id: resumeId,
      experience_level: experienceLevel,
    }),
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error || 'Failed to start interview');
  }

  return response.json();
}

/**
 * Get interview status.
 */
export async function getInterviewStatus(interviewId) {
  const response = await fetch(`${API_BASE}/interview/${interviewId}/status/`);
  
  if (!response.ok) {
    throw new Error('Interview not found');
  }

  return response.json();
}

/**
 * End an interview.
 */
export async function endInterview(interviewId) {
  const response = await fetch(`${API_BASE}/interview/${interviewId}/end/`, {
    method: 'POST',
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error || 'Failed to end interview');
  }

  return response.json();
}

/**
 * Report a cheating event.
 */
export async function reportCheating(interviewId, eventType, confidence, details = {}) {
  const response = await fetch(`${API_BASE}/cheating/report/`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      interview_id: interviewId,
      event_type: eventType,
      confidence,
      details,
    }),
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error || 'Failed to report cheating');
  }

  return response.json();
}

/**
 * Health check.
 */
export async function healthCheck() {
  const response = await fetch(`${API_BASE}/health/`);
  return response.json();
}

/**
 * Create WebSocket connection for interview.
 */
export function createInterviewWebSocket(interviewId, callbacks) {
  const ws = new WebSocket(`${WS_BASE}/interview/${interviewId}/`);

  ws.onopen = () => {
    console.log('WebSocket connected');
    callbacks.onConnected?.();
  };

  ws.onmessage = (event) => {
    if (event.data instanceof Blob) {
      // Binary audio data
      callbacks.onAudio?.(event.data);
    } else {
      // JSON message
      try {
        const data = JSON.parse(event.data);
        
        switch (data.type) {
          case 'connected':
            callbacks.onConnected?.(data);
            break;
          case 'transcript':
            callbacks.onTranscript?.(data.text);
            break;
          case 'warning':
            callbacks.onWarning?.(data);
            break;
          case 'terminated':
            callbacks.onTerminated?.(data);
            break;
          case 'error':
            callbacks.onError?.(data.message);
            break;
          default:
            console.log('Unknown message type:', data);
        }
      } catch (e) {
        console.error('Failed to parse message:', e);
      }
    }
  };

  ws.onerror = (error) => {
    console.error('WebSocket error:', error);
    callbacks.onError?.('Connection error');
  };

  ws.onclose = (event) => {
    console.log('WebSocket closed:', event.code);
    callbacks.onDisconnected?.(event.code);
  };

  return {
    send: (data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(typeof data === 'string' ? data : JSON.stringify(data));
      }
    },
    sendAudio: (audioData) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(audioData);
      }
    },
    close: () => ws.close(),
  };
}
