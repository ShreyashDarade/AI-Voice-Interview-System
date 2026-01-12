import { useState, useRef } from 'react';
import PropTypes from 'prop-types';
import { uploadResume, startInterview } from '../services/api';

const EXPERIENCE_LEVELS = [
  { value: 'fresher', label: 'Fresher (0-1 years)' },
  { value: 'junior', label: 'Junior (1-3 years)' },
  { value: 'mid', label: 'Mid-Level (3-5 years)' },
  { value: 'senior', label: 'Senior (5-8 years)' },
  { value: 'lead', label: 'Lead/Principal (8+ years)' },
];

export default function UploadPage({ onResumeUploaded }) {
  const [file, setFile] = useState(null);
  const [experienceLevel, setExperienceLevel] = useState('fresher');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [parsedResume, setParsedResume] = useState(null);
  const fileInputRef = useRef(null);

  const handleFileChange = (e) => {
    const selectedFile = e.target.files[0];
    if (selectedFile) {
      setFile(selectedFile);
      setError('');
    }
  };

  const handleUpload = async () => {
    if (!file) {
      setError('Please select a resume file');
      return;
    }

    setLoading(true);
    setError('');

    try {
      const resumeData = await uploadResume(file);
      setParsedResume(resumeData);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleStartInterview = async () => {
    if (!parsedResume) return;

    setLoading(true);
    setError('');

    try {
      const interviewData = await startInterview(parsedResume.id, experienceLevel);
      onResumeUploaded({
        resume: parsedResume,
        interview: interviewData,
      });
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ maxWidth: '800px', margin: '0 auto' }}>
      <h2 style={{ textAlign: 'center', marginBottom: '2rem' }}>
        Upload Your Resume
      </h2>

      {error && (
        <div className="toast toast-danger" style={{ position: 'static', marginBottom: '1rem' }}>
          {error}
        </div>
      )}

      {parsedResume ? (
        <div className="card">
          <h3 style={{ marginBottom: '1.5rem' }}>Resume Analysis Complete ✓</h3>

          <div className="grid grid-2" style={{ marginBottom: '1.5rem' }}>
            <div>
              <strong>Name:</strong>
              <p>{parsedResume.candidate_name || 'Not detected'}</p>
            </div>
            <div>
              <strong>Email:</strong>
              <p>{parsedResume.email || 'Not detected'}</p>
            </div>
            <div>
              <strong>Phone:</strong>
              <p>{parsedResume.phone || 'Not detected'}</p>
            </div>
            <div>
              <strong>Experience:</strong>
              <p>{parsedResume.experience_years} years</p>
            </div>
          </div>

          {parsedResume.skills?.length > 0 && (
            <div style={{ marginBottom: '1.5rem' }}>
              <strong>Skills Detected:</strong>
              <div className="skills-container" style={{ marginTop: '0.5rem' }}>
                {parsedResume.skills.slice(0, 15).map((skill) => (
                  <span key={skill} className="skill-tag">{skill}</span>
                ))}
                {parsedResume.skills.length > 15 && (
                  <span className="skill-tag">+{parsedResume.skills.length - 15} more</span>
                )}
              </div>
            </div>
          )}

          <div className="input-group">
            <label htmlFor="experience-level-confirm">Confirm Experience Level</label>
            <select
              id="experience-level-confirm"
              value={experienceLevel}
              onChange={(e) => setExperienceLevel(e.target.value)}
            >
              {EXPERIENCE_LEVELS.map((level) => (
                <option key={level.value} value={level.value}>
                  {level.label}
                </option>
              ))}
            </select>
          </div>

          <div style={{ display: 'flex', gap: '1rem', marginTop: '1.5rem' }}>
            <button
              className="btn btn-secondary"
              onClick={() => {
                setParsedResume(null);
                setFile(null);
              }}
            >
              ← Upload Different
            </button>
            <button
              className="btn btn-primary"
              onClick={handleStartInterview}
              disabled={loading}
              style={{ flex: 1 }}
            >
              {loading ? 'Starting Interview...' : 'Start Interview →'}
            </button>
          </div>
        </div>
      ) : (
        <div className="card">
          <button
            type="button"
            className="file-upload"
            onClick={() => fileInputRef.current?.click()}
            onKeyDown={(e) => e.key === 'Enter' && fileInputRef.current?.click()}
          >
            <input
              type="file"
              ref={fileInputRef}
              onChange={handleFileChange}
              accept=".pdf,.docx,.txt"
            />
            <div className="file-upload-icon">📄</div>
            {file ? (
              <div>
                <strong>{file.name}</strong>
                <p className="file-upload-text">Click to change file</p>
              </div>
            ) : (
              <div>
                <strong>Click or drag to upload</strong>
                <p className="file-upload-text">PDF, DOCX, or TXT (max 10MB)</p>
              </div>
            )}
          </button>

          <div className="input-group" style={{ marginTop: '1.5rem' }}>
            <label htmlFor="experience-level">Experience Level</label>
            <select
              id="experience-level"
              value={experienceLevel}
              onChange={(e) => setExperienceLevel(e.target.value)}
            >
              {EXPERIENCE_LEVELS.map((level) => (
                <option key={level.value} value={level.value}>
                  {level.label}
                </option>
              ))}
            </select>
          </div>

          <button
            className="btn btn-primary"
            onClick={handleUpload}
            disabled={!file || loading}
            style={{ width: '100%', marginTop: '1rem' }}
          >
            {loading ? (
              <>
                <span className="spinner" style={{ width: '20px', height: '20px' }} />
                {' '}Analyzing Resume...
              </>
            ) : (
              'Analyze Resume →'
            )}
          </button>
        </div>
      )}
    </div>
  );
}

UploadPage.propTypes = {
  onResumeUploaded: PropTypes.func.isRequired,
};
