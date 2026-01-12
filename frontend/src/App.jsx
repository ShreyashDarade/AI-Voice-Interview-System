import { useState } from 'react'
import './App.css'
import UploadPage from './pages/UploadPage'
import InterviewPage from './pages/InterviewPage'
import ResultsPage from './pages/ResultsPage'

function App() {
  const [page, setPage] = useState('home')
  const [resumeData, setResumeData] = useState(null)
  const [interviewData, setInterviewData] = useState(null)

  const handleResumeUploaded = (data) => {
    setResumeData(data)
    setPage('interview')
  }

  const handleInterviewComplete = (data) => {
    setInterviewData(data)
    setPage('results')
  }

  const handleStartOver = () => {
    setResumeData(null)
    setInterviewData(null)
    setPage('home')
  }

  return (
    <>
      <nav className="navbar">
        <div className="nav-brand">
          <span>🎯</span>
          <span>AI Interviewer</span>
        </div>
        {page !== 'home' && (
          <button className="btn btn-secondary" onClick={handleStartOver}>
            ← Start Over
          </button>
        )}
      </nav>

      <main className="container">
        {page === 'home' && (
          <>
            <section className="hero">
              <h1>AI-Powered Interviews</h1>
              <p>
                Experience the future of technical interviews with AI-driven
                question generation and real-time voice interaction.
              </p>
              <button className="btn btn-primary" onClick={() => setPage('upload')}>
                Get Started →
              </button>
            </section>

            <div className="grid grid-3" style={{ marginTop: '3rem' }}>
              <div className="card feature-card">
                <div className="feature-icon">📄</div>
                <h3>Smart Resume Analysis</h3>
                <p>AI analyzes your resume to generate personalized questions</p>
              </div>
              <div className="card feature-card">
                <div className="feature-icon">🎤</div>
                <h3>Voice Interviews</h3>
                <p>Natural voice conversations powered by Gemini Live API</p>
              </div>
              <div className="card feature-card">
                <div className="feature-icon">👁️</div>
                <h3>Fair Assessment</h3>
                <p>Eye-tracking ensures interview integrity</p>
              </div>
            </div>
          </>
        )}

        {page === 'upload' && (
          <UploadPage onResumeUploaded={handleResumeUploaded} />
        )}

        {page === 'interview' && resumeData && (
          <InterviewPage 
            resumeData={resumeData} 
            onComplete={handleInterviewComplete}
          />
        )}

        {page === 'results' && interviewData && (
          <ResultsPage 
            data={interviewData} 
            onStartOver={handleStartOver}
          />
        )}
      </main>
    </>
  )
}

export default App
