"""
Interview question generation.

Deterministic templates by default (reproducible, offline). If an LLM provider is configured (``llm`` package) it is
used to tailor questions to the candidate's skills; any failure falls back to the templates.
"""
from __future__ import annotations

import random
from typing import Any, Dict, List

from llm import LLMError, get_client
from llm import safety

CATEGORIES = {'technical', 'behavioral', 'situational', 'project'}
DIFFICULTIES = {'easy', 'medium', 'hard'}


class QuestionGenerator:
    QUESTION_TEMPLATES = {
        'technical': {
            'easy': ["Can you explain what {skill} is and how you've used it?",
                     "What are the basic concepts of {skill}?",
                     "Describe a simple project where you used {skill}."],
            'medium': ["How would you optimize performance in a {skill} application?",
                       "What are best practices when working with {skill}?",
                       "Explain the architecture of a {skill} solution you've built.",
                       "How do you handle errors and debugging in {skill}?"],
            'hard': ["Describe a complex problem you solved using {skill} and your approach.",
                     "How would you design a scalable system using {skill}?",
                     "What are the trade-offs you consider when using {skill} vs alternatives?",
                     "Explain how {skill} works under the hood."],
        },
        'behavioral': {
            'easy': ["Tell me about yourself and your background.", "Why are you interested in this position?",
                     "What motivates you in your work?"],
            'medium': ["Describe a challenging project you worked on and how you handled it.",
                       "Tell me about a time you had to learn a new technology quickly.",
                       "How do you prioritize tasks when working on multiple projects?",
                       "Describe a situation where you had to collaborate with a difficult team member."],
            'hard': ["Tell me about a time you failed and what you learned from it.",
                     "Describe a situation where you had to make a difficult decision with limited information.",
                     "How do you handle conflicting priorities from different stakeholders?"],
        },
        'situational': {
            'easy': ["How would you approach learning a new framework for a project?",
                     "What would you do if you found a bug in production?"],
            'medium': ["How would you handle a situation where requirements change mid-project?",
                       "What would you do if you disagreed with your manager's technical decision?",
                       "How would you onboard a new team member?"],
            'hard': ["How would you design and lead a migration of a legacy system?",
                     "What would you do if a critical team member left during a crucial project phase?",
                     "How would you handle a security breach in production?"],
        },
        'project': {
            'easy': ["Tell me about a project you're proud of.", "What was your role in your most recent project?"],
            'medium': ["What was the most challenging aspect of your recent project?",
                       "How did you ensure code quality in your projects?",
                       "Describe how you collaborated with your team on a project."],
            'hard': ["How did you architect the solution for your most complex project?",
                     "What trade-offs did you make in your project and why?",
                     "How did you measure the success of your project?"],
        },
    }

    EXPERIENCE_DIFFICULTY_MAP = {
        'fresher': {'easy': 0.6, 'medium': 0.3, 'hard': 0.1},
        'junior': {'easy': 0.4, 'medium': 0.5, 'hard': 0.1},
        'mid': {'easy': 0.2, 'medium': 0.5, 'hard': 0.3},
        'senior': {'easy': 0.1, 'medium': 0.4, 'hard': 0.5},
        'lead': {'easy': 0.05, 'medium': 0.35, 'hard': 0.6},
    }
    LEVEL_DESC = {'fresher': '0-1 years, basics and learning ability', 'junior': '1-3 years, practical application',
                  'mid': '3-5 years, problem solving and best practices', 'senior': '5-8 years, architecture and leadership',
                  'lead': '8+ years, strategy and mentorship'}

    def __init__(self, client=None, seed: int | None = None):
        self._client = client
        self._rng = random.Random(seed)

    def generate(self, resume, experience_level: str, num_questions: int = 10) -> List[Dict[str, Any]]:
        skills = resume.skills if isinstance(resume.skills, list) else []
        client = get_client() if self._client is None else (self._client or None)     # client=False disables the LLM
        if client is not None and skills:
            try:
                qs = self._with_llm(client, skills, experience_level, num_questions)
                if qs:
                    return qs
            except LLMError:
                pass
        return self._from_templates(skills, experience_level, num_questions)

    def _with_llm(self, client, skills, level, n) -> List[Dict[str, Any]]:
        system = ("You write interview questions for a voice interview. " + safety.DATA_RULE +
                  ' Return JSON {"questions":[{"text":str,"category":"technical|behavioral|situational|project",'
                  '"difficulty":"easy|medium|hard","skill_tag":str}]}. Questions must be answerable aloud, one idea each, '
                  'and must not ask about protected attributes.')
        user = (f'Write {n} questions for a {level} candidate ({self.LEVEL_DESC.get(level, level)}). '
                f'Include at least 3 skill-specific technical questions.\n'
                + safety.as_data('skills', ', '.join(map(str, skills[:15])), 800))
        data = client.complete_json(system, user, required={'questions': list}, max_tokens=2000)
        out = []
        for q in data['questions'][:n]:
            if not isinstance(q, dict) or not q.get('text'):
                continue
            cat, diff = q.get('category'), q.get('difficulty')
            out.append({'text': safety.clip_str(q['text'], 300),
                        'category': cat if cat in CATEGORIES else 'technical',
                        'difficulty': diff if diff in DIFFICULTIES else 'medium',
                        'skill_tag': safety.clip_str(q.get('skill_tag', ''), 60), 'source': 'llm'})
        return out

    def _from_templates(self, skills, level, n) -> List[Dict[str, Any]]:
        weights = self.EXPERIENCE_DIFFICULTY_MAP.get(level, self.EXPERIENCE_DIFFICULTY_MAP['mid'])
        counts = {'technical': int(n * 0.5), 'behavioral': int(n * 0.2), 'situational': int(n * 0.2),
                  'project': int(n * 0.1) or 1}
        qs = []
        for cat, count in counts.items():
            for _ in range(count):
                diff = self._rng.choices(list(weights), weights=list(weights.values()))[0]
                tpl = self._rng.choice(self.QUESTION_TEMPLATES[cat][diff])
                tag = ''
                if '{skill}' in tpl and skills:
                    tag = str(self._rng.choice(skills))
                    tpl = tpl.replace('{skill}', tag)
                elif '{skill}' in tpl:
                    tpl = tpl.replace('{skill}', 'your main technology')
                qs.append({'text': tpl, 'category': cat, 'difficulty': diff, 'skill_tag': tag, 'source': 'templates'})
        self._rng.shuffle(qs)
        return qs[:n]
