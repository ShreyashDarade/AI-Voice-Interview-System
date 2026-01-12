"""
Question generator based on resume analysis and experience level.
"""
from typing import Dict, List, Any
from django.conf import settings


class QuestionGenerator:
    """
    Generates interview questions based on:
    1. Candidate's skills from resume
    2. Work experience and projects
    3. Experience level filter
    """
    
    # Question templates by category and difficulty
    QUESTION_TEMPLATES = {
        'technical': {
            'easy': [
                "Can you explain what {skill} is and how you've used it?",
                "What are the basic concepts of {skill}?",
                "Describe a simple project where you used {skill}.",
            ],
            'medium': [
                "How would you optimize performance in a {skill} application?",
                "What are best practices when working with {skill}?",
                "Explain the architecture of a {skill} solution you've built.",
                "How do you handle errors and debugging in {skill}?",
            ],
            'hard': [
                "Describe a complex problem you solved using {skill} and your approach.",
                "How would you design a scalable system using {skill}?",
                "What are the trade-offs you consider when using {skill} vs alternatives?",
                "Explain how {skill} works under the hood.",
            ]
        },
        'behavioral': {
            'easy': [
                "Tell me about yourself and your background.",
                "Why are you interested in this position?",
                "What motivates you in your work?",
            ],
            'medium': [
                "Describe a challenging project you worked on and how you handled it.",
                "Tell me about a time you had to learn a new technology quickly.",
                "How do you prioritize tasks when working on multiple projects?",
                "Describe a situation where you had to collaborate with a difficult team member.",
            ],
            'hard': [
                "Tell me about a time you failed and what you learned from it.",
                "Describe a situation where you had to make a difficult decision with limited information.",
                "How do you handle conflicting priorities from different stakeholders?",
            ]
        },
        'situational': {
            'easy': [
                "How would you approach learning a new framework for a project?",
                "What would you do if you found a bug in production?",
            ],
            'medium': [
                "How would you handle a situation where requirements change mid-project?",
                "What would you do if you disagreed with your manager's technical decision?",
                "How would you onboard a new team member?",
            ],
            'hard': [
                "How would you design and lead a migration of a legacy system?",
                "What would you do if a critical team member left during a crucial project phase?",
                "How would you handle a security breach in production?",
            ]
        },
        'project': {
            'easy': [
                "Tell me about a project you're proud of.",
                "What was your role in your most recent project?",
            ],
            'medium': [
                "What was the most challenging aspect of your recent project?",
                "How did you ensure code quality in your projects?",
                "Describe how you collaborated with your team on a project.",
            ],
            'hard': [
                "How did you architect the solution for your most complex project?",
                "What trade-offs did you make in your project and why?",
                "How did you measure the success of your project?",
            ]
        }
    }
    
    # Experience level to difficulty mapping
    EXPERIENCE_DIFFICULTY_MAP = {
        'fresher': {'easy': 0.6, 'medium': 0.3, 'hard': 0.1},
        'junior': {'easy': 0.4, 'medium': 0.5, 'hard': 0.1},
        'mid': {'easy': 0.2, 'medium': 0.5, 'hard': 0.3},
        'senior': {'easy': 0.1, 'medium': 0.4, 'hard': 0.5},
        'lead': {'easy': 0.05, 'medium': 0.35, 'hard': 0.6},
    }
    
    def __init__(self):
        self.gemini_available = bool(settings.GEMINI_API_KEY)
    
    def generate(self, resume, experience_level: str, num_questions: int = 10) -> List[Dict[str, Any]]:
        """
        Generate interview questions based on resume and experience level.
        
        Args:
            resume: Resume model instance
            experience_level: One of 'fresher', 'junior', 'mid', 'senior', 'lead'
            num_questions: Number of questions to generate
            
        Returns:
            List of question dictionaries
        """
        skills = resume.skills if isinstance(resume.skills, list) else []
        
        # Try Gemini-powered generation first
        if self.gemini_available and skills:
            try:
                questions = self._generate_with_gemini(
                    skills=skills,
                    experience_level=experience_level,
                    num_questions=num_questions
                )
                if questions:
                    return questions
            except Exception as e:
                print(f"Gemini question generation failed: {e}")
        
        # Fall back to template-based generation
        return self._generate_from_templates(skills, experience_level, num_questions)
    
    def _generate_with_gemini(
        self,
        skills: List[str],
        experience_level: str,
        num_questions: int
    ) -> List[Dict[str, Any]]:
        """Generate questions using Gemini AI."""
        try:
            import google.generativeai as genai
            import json
            
            genai.configure(api_key=settings.GEMINI_API_KEY)
            model = genai.GenerativeModel('gemini-2.0-flash-001')
            
            level_desc = {
                'fresher': '0-1 years experience, focus on basics and learning ability',
                'junior': '1-3 years experience, practical application of skills',
                'mid': '3-5 years experience, problem-solving and best practices',
                'senior': '5-8 years experience, architecture and leadership',
                'lead': '8+ years experience, strategic thinking and mentorship'
            }
            
            prompt = f"""Generate {num_questions} interview questions for a {experience_level} level candidate.

Candidate Skills: {', '.join(skills[:15])}
Experience Level: {level_desc.get(experience_level, experience_level)}

Requirements:
- Mix of technical, behavioral, and situational questions
- Questions should be relevant to their skills
- Difficulty appropriate for their experience level
- Include at least 2-3 skill-specific technical questions

Return ONLY a valid JSON array:
[
    {{
        "text": "Question text here",
        "category": "technical|behavioral|situational|project",
        "difficulty": "easy|medium|hard",
        "skill_tag": "Relevant skill or empty string"
    }}
]

Return ONLY the JSON array, no markdown or explanation."""

            response = model.generate_content(prompt)
            response_text = response.text.strip()
            
            # Clean up response
            if response_text.startswith('```'):
                response_text = response_text.replace('```json', '').replace('```', '').strip()
            
            return json.loads(response_text)
            
        except Exception as e:
            print(f"Gemini question generation error: {e}")
            return []
    
    def _generate_from_templates(
        self,
        skills: List[str],
        experience_level: str,
        num_questions: int
    ) -> List[Dict[str, Any]]:
        """Generate questions from templates."""
        import random
        
        questions = []
        difficulty_weights = self.EXPERIENCE_DIFFICULTY_MAP.get(
            experience_level, 
            self.EXPERIENCE_DIFFICULTY_MAP['mid']
        )
        
        # Calculate question distribution by category
        category_counts = {
            'technical': int(num_questions * 0.5),
            'behavioral': int(num_questions * 0.2),
            'situational': int(num_questions * 0.2),
            'project': int(num_questions * 0.1) or 1
        }
        
        for category, count in category_counts.items():
            for _ in range(count):
                # Select difficulty based on experience level weights
                difficulty = random.choices(
                    list(difficulty_weights.keys()),
                    weights=list(difficulty_weights.values())
                )[0]
                
                templates = self.QUESTION_TEMPLATES[category][difficulty]
                template = random.choice(templates)
                
                # Substitute skill if needed
                skill_tag = ''
                if '{skill}' in template and skills:
                    skill = random.choice(skills)
                    template = template.replace('{skill}', skill)
                    skill_tag = skill
                
                questions.append({
                    'text': template,
                    'category': category,
                    'difficulty': difficulty,
                    'skill_tag': skill_tag
                })
        
        random.shuffle(questions)
        return questions[:num_questions]
