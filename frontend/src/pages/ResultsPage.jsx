import PropTypes from 'prop-types';
import { useState, useEffect } from 'react';

export default function ResultsPage({ data, onStartOver }) {
  const wasTerminated = data.wasTerminated || data.terminated || data.status === 'terminated';
  const maxStrikes = data.max_strikes || 2; // Get from API or default to 2
  const strikes = data.strikes || 0;
  const [showFullReason, setShowFullReason] = useState(false);

  return (
    <div style={{ maxWidth: '600px', margin: '0 auto', textAlign: 'center' }}>
      <div className="card">
        {wasTerminated ? (
          <>
            <div style={{ fontSize: '4rem', marginBottom: '1rem' }}>🚫</div>
            <h2 style={{ color: 'var(--danger)', marginBottom: '1rem' }}>
              Interview Terminated
            </h2>
            <p style={{ color: 'rgba(255,255,255,0.7)', marginBottom: '1rem' }}>
              This interview was terminated due to integrity violations.
              <br />
              <strong>Strikes received: {strikes} / {maxStrikes}</strong>
            </p>

            {/* Detailed termination reason */}
            {data.termination_reason && (
              <div style={{
                background: 'rgba(220, 38, 38, 0.1)',
                border: '1px solid rgba(220, 38, 38, 0.3)',
                borderRadius: '8px',
                padding: '1rem',
                marginTop: '1.5rem',
                textAlign: 'left'
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
                  <h4 style={{ color: 'var(--danger)', margin: 0 }}>📋 Termination Details</h4>
                  <button
                    onClick={() => setShowFullReason(!showFullReason)}
                    style={{
                      background: 'none',
                      border: 'none',
                      color: 'var(--danger)',
                      cursor: 'pointer',
                      fontSize: '0.9rem'
                    }}
                  >
                    {showFullReason ? 'Hide' : 'Show'} Details
                  </button>
                </div>

                {showFullReason && (
                  <div style={{
                    whiteSpace: 'pre-wrap',
                    fontSize: '0.9rem',
                    color: 'rgba(255,255,255,0.8)',
                    marginTop: '1rem'
                  }}>
                    {data.termination_reason}
                  </div>
                )}

                {/* Show violation summary */}
                {data.events && data.events.length > 0 && showFullReason && (
                  <div style={{ marginTop: '1rem' }}>
                    <strong style={{ color: 'rgba(255,255,255,0.9)' }}>Detected Violations:</strong>
                    <ul style={{ marginTop: '0.5rem', paddingLeft: '1.5rem', textAlign: 'left' }}>
                      {data.events.map((event, idx) => (
                        <li key={idx} style={{ color: 'rgba(255,255,255,0.7)', marginBottom: '0.25rem' }}>
                          Strike {event.strike_number}: {event.event_type.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())} ({(event.confidence * 100).toFixed(0)}% confidence)
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}
          </>
        ) : (
          <>
            <div style={{ fontSize: '4rem', marginBottom: '1rem' }}>🎉</div>
            <h2 style={{ marginBottom: '1rem' }}>Interview Complete!</h2>
            <p style={{ color: 'rgba(255,255,255,0.7)', marginBottom: '2rem' }}>
              Thank you for completing the AI interview session.
              <br />
              Your responses have been recorded for review.
            </p>

            {/* AI-Generated Evaluation */}
            {data.evaluation && (
              <div style={{
                background: 'rgba(34, 197, 94, 0.1)',
                border: '1px solid rgba(34, 197, 94, 0.3)',
                borderRadius: '8px',
                padding: '1.5rem',
                marginBottom: '2rem',
                textAlign: 'left'
              }}>
                <h3 style={{ color: 'var(--success)', marginBottom: '1rem' }}>📊 Interview Evaluation</h3>

                {data.evaluation.overall_rating && (
                  <div style={{ marginBottom: '1rem' }}>
                    <strong>Overall Rating:</strong>
                    <div style={{ display: 'flex', gap: '0.25rem', marginTop: '0.5rem' }}>
                      {[1, 2, 3, 4, 5].map((star) => (
                        <span key={star} style={{ fontSize: '1.5rem', color: star <= data.evaluation.overall_rating ? '#fbbf24' : '#4b5563' }}>
                          ★
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {data.evaluation.summary && (
                  <p style={{ color: 'rgba(255,255,255,0.8)', marginBottom: '1rem' }}>
                    {data.evaluation.summary}
                  </p>
                )}

                {data.evaluation.strengths && data.evaluation.strengths.length > 0 && (
                  <div style={{ marginBottom: '1rem' }}>
                    <strong style={{ color: '#10b981' }}>Strengths:</strong>
                    <ul style={{ marginTop: '0.5rem', paddingLeft: '1.5rem' }}>
                      {data.evaluation.strengths.map((strength, idx) => (
                        <li key={idx} style={{ color: 'rgba(255,255,255,0.7)' }}>{strength}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {data.evaluation.areas_for_improvement && data.evaluation.areas_for_improvement.length > 0 && (
                  <div>
                    <strong style={{ color: '#f59e0b' }}>Areas for Improvement:</strong>
                    <ul style={{ marginTop: '0.5rem', paddingLeft: '1.5rem' }}>
                      {data.evaluation.areas_for_improvement.map((area, idx) => (
                        <li key={idx} style={{ color: 'rgba(255,255,255,0.7)' }}>{area}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}
          </>
        )}

        <div style={{
          background: 'rgba(0,0,0,0.2)',
          borderRadius: '12px',
          padding: '1.5rem',
          marginBottom: '2rem',
        }}>
          <h3 style={{ marginBottom: '1rem' }}>Session Summary</h3>

          <div style={{ textAlign: 'left' }}>
            <p><strong>Status:</strong>
              <span className={`badge ${wasTerminated ? 'badge-danger' : 'badge-success'}`} style={{ marginLeft: '0.5rem' }}>
                {wasTerminated ? 'Terminated' : 'Completed'}
              </span>
            </p>
            <p><strong>Strikes:</strong> {strikes} / {maxStrikes}</p>
            {data.duration_seconds && (
              <p><strong>Duration:</strong> {Math.round(data.duration_seconds / 60)} minutes</p>
            )}
            {data.questions_asked?.length > 0 && (
              <p><strong>Questions:</strong> {data.questions_asked.length} asked</p>
            )}
          </div>
        </div>

        <div style={{ display: 'flex', gap: '1rem', justifyContent: 'center' }}>
          <button className="btn btn-primary" onClick={onStartOver}>
            Start New Interview
          </button>
        </div>
      </div>

      {!wasTerminated && (
        <div className="card" style={{ marginTop: '2rem' }}>
          <h3>💡 What's Next?</h3>
          <p style={{ color: 'rgba(255,255,255,0.7)', marginTop: '1rem' }}>
            Your interview has been recorded and will be reviewed by the hiring team.
            You will receive feedback within 2-3 business days.
          </p>
        </div>
      )}
    </div>
  );
}

ResultsPage.propTypes = {
  data: PropTypes.shape({
    wasTerminated: PropTypes.bool,
    terminated: PropTypes.bool,
    status: PropTypes.string,
    strikes: PropTypes.number,
    max_strikes: PropTypes.number,
    duration_seconds: PropTypes.number,
    questions_asked: PropTypes.arrayOf(PropTypes.object),
    termination_reason: PropTypes.string,
    events: PropTypes.arrayOf(PropTypes.object),
    evaluation: PropTypes.shape({
      overall_rating: PropTypes.number,
      summary: PropTypes.string,
      strengths: PropTypes.arrayOf(PropTypes.string),
      areas_for_improvement: PropTypes.arrayOf(PropTypes.string),
    }),
  }).isRequired,
  onStartOver: PropTypes.func.isRequired,
};
