"""
High-end offline resume parser using PyMuPDF + spaCy NER.
No external API calls - all processing is done locally.
"""
import re
from typing import Dict, List, Any
from datetime import datetime
from pathlib import Path

import fitz  # PyMuPDF


class ResumeParser:
    """
    Production-grade resume parser with hybrid approach:
    1. PyMuPDF for PDF text extraction
    2. Gemini LLM for intelligent parsing
    3. spaCy NER for skills extraction
    """
    
    # Common skills database (subset of Jobzilla 30k+ skills)
    TECH_SKILLS = {
        'python', 'java', 'javascript', 'typescript', 'c++', 'c#', 'go', 'rust',
        'ruby', 'php', 'swift', 'kotlin', 'scala', 'r', 'matlab', 'sql', 'nosql',
        'react', 'angular', 'vue', 'node.js', 'django', 'flask', 'fastapi', 'spring',
        'aws', 'azure', 'gcp', 'docker', 'kubernetes', 'jenkins', 'terraform',
        'git', 'linux', 'mongodb', 'postgresql', 'mysql', 'redis', 'elasticsearch',
        'machine learning', 'deep learning', 'tensorflow', 'pytorch', 'nlp',
        'data science', 'data analysis', 'pandas', 'numpy', 'scikit-learn',
        'rest api', 'graphql', 'microservices', 'agile', 'scrum', 'ci/cd',
        'html', 'css', 'sass', 'bootstrap', 'tailwind', 'figma', 'photoshop',
        'android', 'ios', 'react native', 'flutter', 'unity', 'unreal engine',
        'blockchain', 'solidity', 'web3', 'cybersecurity', 'penetration testing',
        'devops', 'sre', 'cloud computing', 'serverless', 'lambda', 'tableau',
        'power bi', 'excel', 'sap', 'salesforce', 'jira', 'confluence'
    }
    
    SOFT_SKILLS = {
        'leadership', 'communication', 'teamwork', 'problem solving', 'critical thinking',
        'time management', 'project management', 'collaboration', 'adaptability',
        'creativity', 'attention to detail', 'analytical skills', 'presentation',
        'negotiation', 'conflict resolution', 'mentoring', 'stakeholder management'
    }
    
    def __init__(self):
        # Gemini disabled for resume parsing - using offline methods only
        self._nlp = None
    
    @property
    def nlp(self):
        """Lazy load spaCy model."""
        if self._nlp is None:
            try:
                import spacy
                self._nlp = spacy.load('en_core_web_sm')
            except Exception:
                self._nlp = False  # Mark as unavailable
        return self._nlp if self._nlp else None
    
    def parse(self, file_path: str) -> Dict[str, Any]:
        """
        Parse a resume file and extract structured data.
        Uses offline rule-based + NLP parsing (no external API calls).
        
        Args:
            file_path: Path to the resume file
            
        Returns:
            Dictionary with parsed resume data
        """
        file_path = Path(file_path)
        
        if not file_path.exists():
            raise FileNotFoundError(f"Resume file not found: {file_path}")
        
        # Extract raw text based on file type
        raw_text = self._extract_text(file_path)
        
        # Also extract links from PDF
        links = self._extract_links(file_path) if file_path.suffix.lower() == '.pdf' else []
        
        if not raw_text.strip():
            raise ValueError("Could not extract text from resume")
        
        # Use offline rule-based + NLP parsing (no Gemini)
        result = self._parse_with_rules(raw_text)
        result['links'] = links
        
        return result
    
    def _extract_text(self, file_path: Path) -> str:
        """Extract text from various file formats."""
        suffix = file_path.suffix.lower()
        
        if suffix == '.pdf':
            return self._extract_pdf_text(file_path)
        elif suffix == '.docx':
            return self._extract_docx_text(file_path)
        elif suffix == '.txt':
            return file_path.read_text(encoding='utf-8')
        else:
            raise ValueError(f"Unsupported file format: {suffix}")
    
    def _extract_pdf_text(self, file_path: Path) -> str:
        """Extract text from PDF using PyMuPDF with layout preservation."""
        text_blocks = []
        
        # Convert to string and resolve to absolute path for Windows compatibility
        file_str = str(file_path.resolve())
        
        with fitz.open(file_str) as doc:
            for page in doc:
                # Get text with layout preservation
                text = page.get_text("text")
                text_blocks.append(text)
        
        return "\n".join(text_blocks)
    
    def _extract_links(self, file_path: Path) -> List[Dict[str, str]]:
        """Extract embedded links from PDF (LinkedIn, GitHub, Portfolio, etc.)."""
        links = []
        file_str = str(file_path.resolve())
        
        try:
            with fitz.open(file_str) as doc:
                for page_num, page in enumerate(doc):
                    # Get all links from the page
                    link_list = page.get_links()
                    for link in link_list:
                        if 'uri' in link:
                            uri = link['uri']
                            link_type = self._classify_link(uri)
                            links.append({
                                'url': uri,
                                'type': link_type,
                                'page': page_num + 1
                            })
        except Exception:
            pass  # Links are optional, don't fail parsing
        
        return links
    
    def _classify_link(self, url: str) -> str:
        """Classify a URL by its type."""
        url_lower = url.lower()
        if 'linkedin.com' in url_lower:
            return 'linkedin'
        elif 'github.com' in url_lower:
            return 'github'
        elif 'gitlab.com' in url_lower:
            return 'gitlab'
        elif 'stackoverflow.com' in url_lower:
            return 'stackoverflow'
        elif 'twitter.com' in url_lower or 'x.com' in url_lower:
            return 'twitter'
        elif 'mailto:' in url_lower:
            return 'email'
        elif any(d in url_lower for d in ['behance', 'dribbble', 'portfolio']):
            return 'portfolio'
        else:
            return 'other'
    
    def _extract_docx_text(self, file_path: Path) -> str:
        """Extract text from DOCX."""
        try:
            from docx import Document
            doc = Document(file_path)
            return "\n".join([para.text for para in doc.paragraphs])
        except ImportError:
            raise ImportError("python-docx is required for DOCX parsing")
    
    def _parse_with_rules(self, text: str) -> Dict[str, Any]:
        """Parse resume using rule-based extraction + NLP."""
        result = {
            'raw_text': text,
            'name': self._extract_name(text),
            'email': self._extract_email(text),
            'phone': self._extract_phone(text),
            'skills': self._extract_skills(text),
            'education': self._extract_education(text),
            'work_history': self._extract_work_history(text),
            'experience_years': 0
        }
        
        # Calculate experience from work history
        result['experience_years'] = self._calculate_experience(result['work_history'])
        
        return result
    
    def _extract_name(self, text: str) -> str:
        """Extract candidate name using NLP or heuristics."""
        if self.nlp:
            doc = self.nlp(text[:500])  # First part usually has name
            for ent in doc.ents:
                if ent.label_ == 'PERSON':
                    return ent.text
        
        # Fallback: first non-empty line that looks like a name
        lines = text.strip().split('\n')
        for line in lines[:5]:
            line = line.strip()
            if line and len(line.split()) <= 4 and not any(c.isdigit() for c in line):
                if not any(kw in line.lower() for kw in ['resume', 'cv', 'curriculum']):
                    return line
        
        return ''
    
    def _extract_email(self, text: str) -> str:
        """Extract email address."""
        pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
        match = re.search(pattern, text)
        return match.group(0) if match else ''
    
    def _extract_phone(self, text: str) -> str:
        """Extract phone number."""
        patterns = [
            r'\+?1?[-.\s]?\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}',
            r'\+91[-.\s]?[0-9]{10}',
            r'[0-9]{10}'
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(0)
        return ''
    
    def _extract_skills(self, text: str) -> List[str]:
        """Extract skills using keyword matching."""
        text_lower = text.lower()
        found_skills = []
        
        # Check for technical skills
        for skill in self.TECH_SKILLS:
            if skill in text_lower:
                found_skills.append(skill.title())
        
        # Check for soft skills
        for skill in self.SOFT_SKILLS:
            if skill in text_lower:
                found_skills.append(skill.title())
        
        return list(set(found_skills))
    
    def _extract_education(self, text: str) -> List[Dict[str, str]]:
        """Extract education information."""
        education = []
        
        # Common degree patterns
        degree_patterns = [
            r"(B\.?S\.?|Bachelor(?:'s)?|M\.?S\.?|Master(?:'s)?|Ph\.?D\.?|MBA|B\.?Tech|M\.?Tech|B\.?E\.?|M\.?E\.?)[^,\n]*(?:in\s+)?([^,\n]+)?",
        ]
        
        for pattern in degree_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                if isinstance(match, tuple):
                    degree = ' '.join(match).strip()
                else:
                    degree = match.strip()
                if degree:
                    education.append({'degree': degree, 'institution': '', 'year': ''})
        
        return education[:3]  # Limit to 3 entries
    
    def _extract_work_history(self, text: str) -> List[Dict[str, str]]:
        """Extract work history."""
        work_history = []
        
        # Look for date ranges (indicative of job periods)
        date_pattern = r'(\d{4})\s*[-–]\s*(Present|\d{4})'
        matches = re.findall(date_pattern, text, re.IGNORECASE)
        
        for start, end in matches[:5]:  # Limit to 5 jobs
            work_history.append({
                'title': '',
                'company': '',
                'duration': f'{start} - {end}',
                'description': ''
            })
        
        return work_history
    
    def _calculate_experience(self, work_history: List[Dict]) -> float:
        """Calculate total years of experience from work history."""
        total_years = 0
        current_year = datetime.now().year
        
        for job in work_history:
            duration = job.get('duration', '')
            match = re.search(r'(\d{4})\s*[-–]\s*(Present|\d{4})', duration, re.IGNORECASE)
            if match:
                start = int(match.group(1))
                end = current_year if match.group(2).lower() == 'present' else int(match.group(2))
                total_years += max(0, end - start)
        
        return float(total_years)
